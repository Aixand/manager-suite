"""Restore an exact native conversation after its recorded owner has stopped."""
import asyncio
from copy import deepcopy

from .agents import AgentEnvironments
from .frontend import startup_lock, incomplete, shared_opencode, agent_reference
from .models import ConversationError, PreparedLaunch, digest, operation_key, receipt


async def resume_conversation(client, reference, *, operation_id):
    operation_key(operation_id)
    saved = await asyncio.to_thread(client._saved_session, reference)
    prior = reference['recovery']['connection_ref']
    with startup_lock(client, 'resume:' + reference['session_id']):
        successor = client.store.get('session-successor', reference['session_id'])
        if successor is not None:
            return await resume_conversation(client, successor,
                operation_id='resume-successor:' + digest([operation_id, successor['session_id']])[7:])
        # A live owner must retain its conversation. Never replace a slow worker.
        observation = await client.inspect_runtime(prior['runtime_ref'])
        if observation.get('runtime_ref') != prior['runtime_ref']:
            raise ConversationError('conflict', 'The recorded runtime identity changed')
        if observation.get('state') == 'running':
            return await client.attach_conversation(reference)
        if observation.get('state') not in ('stopped', 'exited', 'failed'):
            raise ConversationError('uncertain', 'The previous runtime is not confirmed stopped', uncertain=True)
        intent = {'method': 'resume_conversation', 'session_ref': deepcopy(reference)}
        row, _ = client.store.claim(operation_id, intent)
        if row['status'] == 'completed':
            restored = row['result']['conversation_ref']
            client.store.put('session-successor', reference['session_id'], restored)
            return await resume_conversation(client, restored,
                operation_id='resume-successor:' + digest([operation_id, restored['session_id']])[7:])
        if row['status'] in ('failed', 'unsupported'):
            incomplete(client, operation_id, row, row.get('recovery', {}))
        suffix = digest(operation_id)[7:]
        launch_id, restore_id = 'resume-launch:' + suffix, 'resume-session:' + suffix
        connection = None
        try:
            try:
                launched = await client.operation(launch_id)
            except ConversationError as error:
                if error.code != 'not_found':
                    raise
                base = PreparedLaunch.from_dict(client.store.connection_preparation(prior))
                descriptor = base.transport.get('agent_environment_ref')
                if not descriptor or base.adapter_id not in ('native-codex', 'opencode-native'):
                    raise ConversationError('unsupported', 'Automatic restore requires a managed native Codex or OpenCode environment')
                agents = AgentEnvironments(descriptor['provider'], config=descriptor['config'], root=descriptor['root'])
                target = await asyncio.to_thread(agents.resolve, descriptor['target']['environment_id'])
                for key in ('registry', 'home', 'environment_id'):
                    if target[key] != descriptor['target'][key]:
                        raise ConversationError('conflict', 'The original native environment identity changed')
                if base.adapter_id == 'opencode-native' and reference['resource_snapshot_ref'] is None:
                    launched = await shared_opencode(client, agent_reference(target, agents.identity),
                        reference['cwd'], agents, launch_id)
                else:
                    current = await asyncio.to_thread(agents.prepare_native, target['environment_id'])
                    prepared = await client.prepare_restore(reference, base)
                    prepared.transport['agent_environment_ref'] = current
                    if base.adapter_id == 'opencode-native':
                        prepared.argv = [current['executable']]
                    launched = await client.launch(prepared, operation_id=launch_id)
            if launched['status'] != 'completed':
                incomplete(client, operation_id, launched, {'launch_operation_id': launch_id})
            replacement = launched['result']['connection_ref']
            connection = await client.connect(replacement)
            restored = await connection.restore_session(reference, operation_id=restore_id)
            if restored['status'] != 'completed':
                incomplete(client, operation_id, restored, {'launch_operation_id': launch_id})
            conversation = await connection.attach_conversation(restored['result']['session_ref'])
            client.store.put('session-successor', reference['session_id'], conversation.reference)
            client.store.finish(receipt(operation_id, 'completed', result={'conversation_ref': conversation.reference},
                recovery={'launch_operation_id': launch_id, 'runtime_ref': replacement['runtime_ref']}))
            connection = None
            return conversation
        finally:
            if connection is not None:
                await connection.disconnect()
