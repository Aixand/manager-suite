"""Real worker/IPC/native-lease handover, using only an offline Codex executable."""
import asyncio
import json
import unittest

import test_codex_local as local
from agent_manager.errors import BusyError
from agent_manager.storage.locks import locks


class NativeSigninTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = local.LocalCodexTests.asyncSetUp
    asyncTearDown = local.LocalCodexTests.asyncTearDown
    launch = local.LocalCodexTests.launch

    async def start(self):
        self.assertEqual((await self.launch())['status'], 'completed')
        self.connection = await self.client.connect(self.ref)
        return await self.connection.create_conversation(operation_id='create')

    async def finish(self, conversation, turn):
        async with asyncio.timeout(5):
            while (await conversation.state())['active_turn']:
                await asyncio.sleep(.02)
        self.assertEqual((await conversation.turn_result(turn))['status'], 'completed')

    async def test_real_idle_worker_releases_lease_and_same_chat_restores_without_replay(self):
        try:
            from orchestration_mcp.executions import ExecutionServices
        except ModuleNotFoundError as error:
            if error.name == 'orchestration_mcp':
                self.skipTest('Install orchestration for the cross-component handover test')
            raise
        conversation = await self.start()
        executions = ExecutionServices(self.root / 'handover')
        executions._conversation_client = self.client
        self.addCleanup(lambda: executions._db and executions._db.close())
        executions.save_binding(dict(id='parent', mode='conversation', provider='codex',
            environment_id=self.identifier, reference=conversation.reference))
        handover = executions.signin_handover()
        sent = await conversation.send('one message', operation_id='send')
        result = await handover.advance('codex', self.identifier, 'signin')
        self.assertFalse(result['ready'])
        await self.finish(conversation, sent['result']['turn_id'])
        with self.assertRaises(BusyError):
            self.manager.login('work')
        saved = conversation.reference
        result = await handover.advance('codex', self.identifier, 'signin')
        self.assertTrue(result['ready'], result)
        with locks(self.manager.lock_root, 'environment-' + self.identifier):
            pass
        progress = []
        prepared = self.manager.prepare_provider_operation(dict(kind='profile-login', profile='work'))
        login = await asyncio.to_thread(self.manager.perform_provider_operation, prepared, progress=progress.append)
        self.assertEqual(login['state'], 'signed-in')
        self.assertIn('awaiting-user', [value['phase'] for value in progress])
        await handover.release('codex', self.identifier, 'signin')
        binding = await executions.reconnect_conversation('parent')
        restored = await executions.chat(binding)
        self.connection = restored.connection
        self.ref = restored.reference['recovery']['connection_ref']
        self.assertEqual(restored.reference['native_session_id'], saved['native_session_id'])
        self.assertIsNone((await restored.state())['active_turn'])
        turns = [json.loads(line) for line in (self.native / 'turns.jsonl').read_text().splitlines()]
        self.assertEqual(len(turns), 1, 'Restoring must not replay a message')

    async def test_background_work_blocks_stop_and_cancel_reopens_the_same_worker(self):
        conversation = await self.start()
        marker = self.native / 'background-work'
        marker.touch()
        result = await self.connection.prepare_signin_stop(operation_id='signin')
        self.assertFalse(result['ready'])
        self.assertIn(dict(kind='background_commands'), result['blockers'])
        self.assertFalse((await conversation.state())['draining'])
        marker.unlink()
        self.assertTrue((await self.connection.prepare_signin_stop(operation_id='signin'))['ready'])
        await self.connection.cancel_signin_stop(operation_id='signin')
        sent = await conversation.send('after cancellation', operation_id='after-cancel')
        self.assertEqual(sent['status'], 'accepted')
        await self.finish(conversation, sent['result']['turn_id'])
