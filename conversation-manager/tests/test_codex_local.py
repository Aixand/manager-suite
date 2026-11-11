import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import shutil
import signal
from unittest.mock import patch

try:
    from agent_manager import Manager
except ImportError:
    Manager = None
from conversation_manager import AgentEnvironments, Client, ConversationError, StandaloneLauncher


@unittest.skipUnless(Manager is not None, 'Requires Agent Manager')
class LocalCodexTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cm-codex-test-')
        self.root = Path(self.tmp.name)
        fixture = Path(__file__).parent / 'fixtures/codex_local.py'
        if os.name == 'nt':
            launcher = os.environ.get('CM_TEST_COMMAND_LAUNCHER')
            if not launcher:
                self.skipTest('Set CM_TEST_COMMAND_LAUNCHER to the native command launcher')
            executable = self.root / 'codex.exe'
            shutil.copyfile(launcher, executable)
            executable.with_suffix('.fixture').write_text(sys.executable + '\n' + str(fixture) + '\n')
            executable.with_name('codex.aixand-command.json').write_text(json.dumps({
                'root': str(self.root), 'commands': {'codex': {
                    'program': sys.executable, 'args': ['-B', str(fixture)]}}}))
        else:
            executable = self.root / 'codex'
            executable.write_text('#!' + sys.executable + '\n' + fixture.read_text())
            executable.chmod(0o700)
        self.config = self.root / 'providers.toml'
        self.config.write_text('[providers.codex]\nexecutable=' + json.dumps(str(executable)) + '\n')
        self.manager = Manager('codex', config=self.config, root=self.root / 'agents')
        self.manager.new_profile('work')
        self.identifier = self.manager.resolve_profile('work').environment_id
        self.native = self.manager.home('work')
        self.agents = AgentEnvironments('codex', config=self.config, root=self.root / 'agents')
        self.client = Client(self.root / 'conversations', agent_config=self.config, agent_root=self.root / 'agents')
        self.launcher = StandaloneLauncher(self.client.home)
        self.connection = self.ref = None

    async def asyncTearDown(self):
        if self.connection:
            await self.connection.disconnect()
        if self.ref:
            await self.launcher.terminate(self.ref['runtime_ref'], operation_id='cleanup')
        self.tmp.cleanup()

    async def launch(self, operation_id='launch'):
        launched = await self.client.launch_codex(self.identifier, cwd=self.root,
            agents=self.agents, operation_id=operation_id)
        self.ref = launched.get('result', {}).get('connection_ref') or launched.get('recovery', {}).get('connection_ref')
        return launched

    async def test_real_managers_launch_send_detach_reconnect_and_stop(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'do-not-use-ambient-key'}):
            launched = await self.launch()
        self.assertEqual(launched['status'], 'completed', launched)
        self.connection = await self.client.connect(self.ref)
        conversation = await self.connection.create_conversation(operation_id='create')
        sent = await conversation.send('hello', operation_id='send')
        await self.connection.disconnect()
        self.connection = await Client(self.client.home).connect(self.ref)
        attached = await self.connection.attach_conversation(conversation.reference)
        async with asyncio.timeout(5):
            async for event in attached.events():
                if event['kind'] == 'turn.completed':
                    break
        result = await attached.turn_result(sent['result']['turn_id'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['final_text'], 'fixture:hello')
        self.assertEqual(attached.reference, conversation.reference)
        self.assertEqual(await self.launch(), launched)
        launches = [json.loads(line) for line in (self.native / 'launches.jsonl').read_text().splitlines()]
        self.assertEqual(len(launches), 1)
        self.assertEqual(launches[0]['home'], str(self.native))
        self.assertFalse(launches[0]['ambient_key'])
        self.assertNotIn(b'do-not-use-ambient-key', self.client.store.path.read_bytes())
        from agent_manager.storage.locks import locks
        from agent_manager.errors import BusyError
        with self.assertRaises(BusyError):
            with locks(self.manager.lock_root, 'environment-' + self.identifier):
                pass
        stopped = await self.launcher.terminate(self.ref['runtime_ref'], operation_id='stop')
        self.assertEqual(stopped['status'], 'completed')
        with locks(self.manager.lock_root, 'environment-' + self.identifier):
            pass

    async def test_signed_out_home_fails_and_cleans_up(self):
        (self.native / 'signed-out').touch()
        result = await self.launch()
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['error']['code'], 'authentication')

    async def test_two_workers_share_environment_until_both_stop(self):
        first = await self.launch()
        self.assertEqual(first['status'], 'completed', first)
        second = await self.client.launch_codex(self.identifier, cwd=self.root, agents=self.agents,
                                               operation_id='second-launch')
        self.assertEqual(second['status'], 'completed', second)
        second_ref = second['result']['connection_ref']
        self.addAsyncCleanup(self.launcher.terminate, second_ref['runtime_ref'], operation_id='second-cleanup')
        from agent_manager.storage.locks import locks
        from agent_manager.errors import BusyError
        await self.launcher.terminate(self.ref['runtime_ref'], operation_id='first-stop')
        with self.assertRaises(BusyError):
            with locks(self.manager.lock_root, 'environment-' + self.identifier):
                pass
        await self.launcher.terminate(second_ref['runtime_ref'], operation_id='second-stop')
        with locks(self.manager.lock_root, 'environment-' + self.identifier):
            pass

    async def test_worker_failure_stops_native_and_releases_lease(self):
        result = await self.launch()
        self.assertEqual(result['status'], 'completed', result)
        native = json.loads((self.native / 'launches.jsonl').read_text().splitlines()[0])
        from aixand_local_runtime import process_start
        native_identity = process_start(native['pid'])
        os.kill(self.ref['runtime_ref']['pid'], signal.SIGTERM if os.name == 'nt' else signal.SIGKILL)
        async with asyncio.timeout(8):
            while process_start(native['pid']) == native_identity:
                await asyncio.sleep(.03)
        from agent_manager.storage.locks import locks
        from agent_manager.errors import BusyError
        async with asyncio.timeout(8):
            while True:
                try:
                    with locks(self.manager.lock_root, 'environment-' + self.identifier):
                        break
                except BusyError:
                    await asyncio.sleep(.02)

    async def test_cli_launch_uses_the_same_public_contract(self):
        process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-m', 'conversation_manager',
            '--home', str(self.client.home), 'codex', 'launch', '--environment-id', self.identifier,
            '--cwd', str(self.root), '--agent-config', str(self.config), '--agent-root', str(self.root / 'agents'),
            '--operation-id', 'cli-launch', '--json', stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        output, error = await process.communicate()
        self.assertEqual(process.returncode, 0, error.decode())
        launched = json.loads(output)
        self.ref = launched['result']['connection_ref']
        self.assertEqual(launched['status'], 'completed')

    async def test_keyed_retry_rejects_different_intent(self):
        result = await self.launch()
        self.assertEqual(result['status'], 'completed', result)
        with self.assertRaises(ConversationError) as caught:
            await self.client.launch_codex(self.identifier, cwd=self.root, agents=self.agents,
                settings={'model': 'other'}, operation_id='launch')
        self.assertEqual(caught.exception.code, 'conflict')

    async def test_stale_preparation_is_rejected_before_backend_spawn(self):
        descriptor = self.agents.prepare_codex(self.identifier)
        self.config.write_text(self.config.read_text() + 'run_args=["--changed"]\n')
        from conversation_manager import PreparedLaunch
        result = await self.client.launch(PreparedLaunch(backend_id='codex', adapter_id='native-codex',
            argv=[], cwd=str(self.root), transport={'kind': 'managed-codex', 'agent_environment_ref': descriptor}),
            operation_id='stale')
        self.ref = result.get('recovery', {}).get('connection_ref')
        self.assertNotEqual(result['status'], 'completed')
        self.assertFalse((self.native / 'launches.jsonl').exists())

    async def frontend_start(self, *, operation_id='start', settings=None, agent=None):
        if agent is None:
            agent = (await self.client.available_agents(providers=('codex',)))[0]['reference']
        try:
            conversation = await self.client.start_conversation(agent=agent, cwd=self.root,
                settings=settings, operation_id=operation_id)
        except ConversationError as error:
            self.ref = error.recovery.get('connection_ref') or self.ref
            raise
        self.ref = conversation.startup_receipt['recovery']['connection_ref']
        self.connection = conversation.connection
        return conversation

    async def test_frontend_only_api_starts_sends_and_reattaches(self):
        from conversation_manager import ConversationClient
        self.assertIs(ConversationClient, Client)
        conversation = await self.frontend_start()
        sent = await conversation.send('frontend', operation_id='frontend-send')
        await conversation.disconnect()
        second = Client(self.client.home)
        attached = await second.attach_conversation(conversation.reference)
        self.connection = attached.connection
        async with asyncio.timeout(5):
            async for event in attached.events():
                if event['kind'] == 'turn.completed':
                    break
        self.assertEqual((await attached.turn_result(sent['result']['turn_id']))['final_text'], 'fixture:frontend')
        listings = await second.conversations(detailed=True)
        self.assertEqual(listings[0]['availability'], 'available')
        self.assertEqual(listings[0]['reference'], conversation.reference)

    async def test_frontend_retry_recovers_without_agent_manager_lookup(self):
        conversation = await self.frontend_start()
        await conversation.disconnect()
        agent = (await self.client.available_agents(providers=('codex',)))[0]['reference']
        with patch('conversation_manager.frontend.AgentEnvironments', side_effect=AssertionError('Must not reselect')):
            again = await self.frontend_start(agent=agent)
        self.assertEqual(again.reference, conversation.reference)
        self.assertEqual(len((self.native / 'launches.jsonl').read_text().splitlines()), 1)
        self.assertEqual(len((self.native / 'creations.jsonl').read_text().splitlines()), 1)

    async def test_failed_creation_retains_runtime_for_explicit_cleanup(self):
        (self.native / 'reject-create').touch()
        with self.assertRaises(ConversationError) as caught:
            await self.frontend_start()
        self.assertIsNotNone(caught.exception.recovery['runtime_ref'])
        operation = await self.client.operation('start')
        self.assertEqual(operation['status'], 'failed')
        with self.assertRaises(ConversationError):
            await self.frontend_start()
        self.assertEqual(len((self.native / 'creations.jsonl').read_text().splitlines()), 1)
        stopped = await self.client.stop_startup('start', operation_id='stop-failed-start')
        self.assertEqual(stopped['status'], 'completed')

    async def test_uncertain_creation_is_never_resent(self):
        (self.native / 'disconnect-create').touch()
        for _ in range(2):
            with self.assertRaises(ConversationError) as caught:
                await self.frontend_start()
            self.assertTrue(caught.exception.uncertain)
        self.assertEqual(len((self.native / 'creations.jsonl').read_text().splitlines()), 1)
        self.assertEqual(len((self.native / 'launches.jsonl').read_text().splitlines()), 1)

    async def test_cancelled_wait_can_recover_completed_creation(self):
        (self.native / 'slow-create').touch()
        agent = (await self.client.available_agents(providers=('codex',)))[0]['reference']
        pending = asyncio.create_task(self.client.start_conversation(agent=agent, cwd=self.root, operation_id='start'))
        async with asyncio.timeout(5):
            while not (self.native / 'creations.jsonl').exists():
                await asyncio.sleep(.01)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.ref = (await self.client.operation('start'))['recovery']['connection_ref']
        async with asyncio.timeout(5):
            while True:
                try:
                    conversation = await self.frontend_start(agent=agent)
                    break
                except ConversationError as error:
                    if not error.uncertain:
                        raise
                    await asyncio.sleep(.02)
        self.assertEqual(conversation.reference['backend_id'], 'codex')
        self.assertEqual(len((self.native / 'creations.jsonl').read_text().splitlines()), 1)

    async def test_frontend_discovery_reports_support_without_launching(self):
        self.manager.new_profile('second')
        entries = await self.client.available_agents()
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(item['conversation']['supported'] for item in entries))
        self.assertTrue(all(item['conversation']['verification'] == 'not-verified-with-account' for item in entries))
        self.assertFalse((self.native / 'launches.jsonl').exists())

    async def test_changed_startup_intent_is_rejected(self):
        conversation = await self.frontend_start()
        await conversation.disconnect()
        with self.assertRaises(ConversationError) as caught:
            await self.frontend_start(settings={'model': 'changed'})
        self.assertEqual(caught.exception.code, 'conflict')

    async def test_concurrent_startup_is_serialized(self):
        (self.native / 'slow-create').touch()
        agent = (await self.client.available_agents(providers=('codex',)))[0]['reference']
        pending = asyncio.create_task(self.frontend_start(agent=agent))
        async with asyncio.timeout(5):
            while not (self.native / 'creations.jsonl').exists():
                await asyncio.sleep(.01)
        with self.assertRaises(ConversationError) as caught:
            await self.client.start_conversation(agent=agent, cwd=self.root, operation_id='start')
        self.assertEqual(caught.exception.code, 'busy')
        await pending
        self.assertEqual(len((self.native / 'creations.jsonl').read_text().splitlines()), 1)

    async def test_other_providers_are_discoverable_but_not_startable(self):
        manager = Manager('claude', config=self.config, root=self.root / 'agents')
        manager.new_profile('claude-work')
        entry = (await self.client.available_agents(providers=('claude',)))[0]
        self.assertFalse(entry['conversation']['supported'])
        with self.assertRaises(ConversationError) as caught:
            await self.client.start_conversation(agent=entry['reference'], cwd=self.root, operation_id='unsupported')
        self.assertEqual(caught.exception.code, 'unsupported')
        self.assertFalse((self.native / 'launches.jsonl').exists())

    async def test_frontend_cli_discovers_starts_attaches_and_stops(self):
        async def command(*arguments):
            process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-m', 'conversation_manager',
                '--home', str(self.client.home), *arguments,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            output, error = await process.communicate()
            self.assertEqual(process.returncode, 0, error.decode())
            return json.loads(output)
        agents = await command('agents', '--agent-config', str(self.config), '--agent-root',
            str(self.root / 'agents'), '--provider', 'codex', '--json')
        started = await command('conversation', 'start', '--agent', json.dumps(agents[0]['reference']),
            '--cwd', str(self.root), '--operation-id', 'cli-start', '--json')
        self.ref = started['startup_receipt']['recovery']['connection_ref']
        attached = await command('conversation', 'attach', '--conversation', json.dumps(started['conversation_ref']), '--json')
        self.assertEqual(attached['conversation_ref'], started['conversation_ref'])
        stopped = await command('conversation', 'stop-startup', '--startup-operation-id', 'cli-start',
            '--operation-id', 'cli-stop', '--json')
        self.assertEqual(stopped['status'], 'completed')
        listings = await command('conversation', 'list', '--detailed', '--json')
        self.assertEqual(listings[0]['availability'], 'unavailable')

    async def test_stopped_startup_never_launches_a_replacement(self):
        agent = (await self.client.available_agents(providers=('codex',)))[0]['reference']
        conversation = await self.frontend_start(agent=agent)
        await conversation.disconnect()
        await self.client.stop_startup('start', operation_id='stop-start')
        with self.assertRaises(ConversationError) as caught:
            await self.frontend_start(agent=agent)
        self.assertEqual(caught.exception.operation_id, 'start')
        self.assertEqual(len((self.native / 'launches.jsonl').read_text().splitlines()), 1)

    async def test_secrets_are_rejected_before_startup_is_persisted(self):
        with self.assertRaises(ConversationError):
            await self.frontend_start(settings={'api_key': 'do-not-persist'})
        if self.client.store.path.exists():
            self.assertNotIn(b'do-not-persist', self.client.store.path.read_bytes())
        self.assertFalse((self.native / 'launches.jsonl').exists())

    async def test_cleanup_recovers_runtime_from_child_launch_checkpoint(self):
        conversation = await self.frontend_start()
        await conversation.disconnect()
        from conversation_manager.models import receipt
        launch_id = conversation.startup_receipt['recovery']['launch_operation_id']
        self.client.store.finish(receipt('start', 'uncertain', recovery={'launch_operation_id': launch_id}))
        stopped = await self.client.stop_startup('start', operation_id='stop-incomplete-checkpoint')
        self.assertEqual(stopped['status'], 'completed')

    async def test_reboot_restore_keeps_native_identity_and_does_not_replay_messages(self):
        original = await self.client.start_conversation(
            agent=(await self.client.available_agents(providers=('codex',)))[0]['reference'],
            cwd=self.root, operation_id='start-before-reboot')
        self.connection, self.ref = original.connection, original.reference['recovery']['connection_ref']
        sent = await original.send('before reboot', operation_id='send-before-reboot')
        async with asyncio.timeout(5):
            while (await original.state()).get('active_turn'):
                await asyncio.sleep(.02)
        history = await self.client.history(original.reference)
        await original.disconnect()
        await self.launcher.terminate(self.ref['runtime_ref'], operation_id='simulate-reboot')
        fresh = Client(self.client.home, agent_config=self.config, agent_root=self.root / 'agents')
        from unittest.mock import AsyncMock
        with patch.object(fresh, 'connect', new=AsyncMock(side_effect=AssertionError('Dead-worker IPC must not run'))):
            with self.assertRaises(ConversationError) as stopped:
                await fresh.attach_conversation(original.reference)
            self.assertEqual(stopped.exception.code, 'runtime_stopped')
        restored = await fresh.resume_conversation(original.reference, operation_id='resume-after-reboot')
        self.connection, self.ref = restored.connection, restored.reference['recovery']['connection_ref']
        self.assertEqual(restored.reference['native_session_id'], original.reference['native_session_id'])
        self.assertNotEqual(restored.reference['connection_id'], original.reference['connection_id'])
        self.assertEqual((await fresh.history(restored.reference))['entries'], history['entries'])
        self.assertEqual((await restored.turn_result(sent['result']['turn_id']))['final_text'], 'fixture:before reboot')
        self.assertFalse((await restored.state()).get('active_turn'))
        repeated = await fresh.resume_conversation(original.reference, operation_id='resume-after-reboot')
        self.assertEqual(repeated.reference, restored.reference)
        await repeated.disconnect()
        stale = await fresh.resume_conversation(original.reference, operation_id='another-resume-request')
        self.assertEqual(stale.reference, restored.reference)
        await stale.disconnect()
        launches = (self.native / 'launches.jsonl').read_text().splitlines()
        self.assertEqual(len(launches), 2)
        self.assertEqual(len((self.native / 'creations.jsonl').read_text().splitlines()), 1)
        self.assertEqual(len((self.native / 'turns.jsonl').read_text().splitlines()), 1)

    async def test_resume_keeps_a_live_owner(self):
        original = await self.client.start_conversation(
            agent=(await self.client.available_agents(providers=('codex',)))[0]['reference'],
            cwd=self.root, operation_id='live-start')
        self.connection, self.ref = original.connection, original.reference['recovery']['connection_ref']
        attached = await self.client.resume_conversation(original.reference, operation_id='live-resume')
        self.assertEqual(attached.reference, original.reference)
        self.assertEqual(len((self.native / 'launches.jsonl').read_text().splitlines()), 1)
        await attached.disconnect()

    async def test_unknown_owner_never_starts_a_replacement(self):
        from unittest.mock import AsyncMock
        original = await self.client.start_conversation(
            agent=(await self.client.available_agents(providers=('codex',)))[0]['reference'],
            cwd=self.root, operation_id='unknown-owner-start')
        self.connection, self.ref = original.connection, original.reference['recovery']['connection_ref']
        observation = {'runtime_ref': self.ref['runtime_ref'], 'state': 'unknown'}
        with patch.object(self.client, 'inspect_runtime', new=AsyncMock(return_value=observation)), \
                patch.object(self.client, 'launch', new=AsyncMock()) as launch:
            with self.assertRaises(ConversationError) as unknown:
                await self.client.resume_conversation(original.reference, operation_id='unknown-owner-resume')
            self.assertTrue(unknown.exception.uncertain)
            launch.assert_not_awaited()
