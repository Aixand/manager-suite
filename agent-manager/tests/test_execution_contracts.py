"""External integration launch settings and authoritative execution observations."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from agent_manager import Client, Error
from agent_manager.runtime.native.configuration import apply, normalize
from agent_manager.runtime.runs import finish_run, read_run, update_run
from tests.test_budget_interfaces import BudgetFixture


class NativeSettingsTests(BudgetFixture, unittest.TestCase):
    def test_config_is_literal_and_precedes_native_subcommands_and_separator(self):
        settings = {'mcp_servers.example.args': ['$(touch /tmp/unwanted)', 'a b'], 'hooks.Stop': [{'hooks': [{'type': 'mcp_tool'}]}]}
        args = apply(self.manager, ['--yolo', 'resume', 'session'], settings)
        self.assertEqual(args[0], '--yolo')
        self.assertEqual(args[-2:], ['resume', 'session'])
        import tomllib
        parsed = [tomllib.loads(args[i + 1]) for i, item in enumerate(args) if item == '-c']
        self.assertEqual(next(row['mcp_servers']['example']['args'] for row in parsed if 'mcp_servers' in row), settings['mcp_servers.example.args'])
        self.assertEqual(apply(self.manager, ['--', 'prompt'], {'model': 'x'})[-2:], ['--', 'prompt'])

    def test_invalid_values_and_unqualified_provider_fail_before_launch(self):
        for value in ({'#': True}, {'x\ny': True}, {'key': None}, {'key': float('inf')}, {'key': object()}):
            with self.subTest(value=value), self.assertRaises(ValueError): normalize(self.manager, value)
        with self.assertRaises(Error): normalize(self.managers['claude'], {'model': 'x'})

    def test_overlapping_keys_are_rejected_and_mapping_order_is_not_launch_intent(self):
        for values in ({'model': 'x', '"model"': 'y'}, {'mcp_servers.example': {}, 'mcp_servers.example.command': 'x'}):
            with self.assertRaisesRegex(ValueError, 'overlap or repeat'): normalize(self.manager, values)
        first = {'model': 'x', 'model_reasoning_effort': 'low'}
        self.assertEqual(apply(self.manager, [], first), apply(self.manager, [], dict(reversed(list(first.items())))))

    def test_direct_launch_accepts_explicit_native_settings_without_persisting_preferences(self):
        with patch.object(self.manager, '_invoke_native', return_value=0) as invoked:
            self.manager._run_native('work', [], native_config={'model_reasoning_effort': 'low'})
        self.assertIn('model_reasoning_effort="low"', invoked.call_args.args[1])
        self.assertNotIn('native_config', self.manager.state())


class ExecutionObservationTests(BudgetFixture, unittest.IsolatedAsyncioTestCase):
    def client(self):
        runtime = SimpleNamespace(connection_options={'home': '/tmp/fixture-runtime'}, connect=AsyncMock(),
                                  inspect=AsyncMock(return_value=SimpleNamespace(lifecycle='running')))
        client = Client('codex', config=self.config, root=self.root, runtime=runtime)
        client.reconcile = AsyncMock()
        return client

    async def launch(self, client, **options):
        def accepted(manager, run_id): update_run(manager, run_id, session_id='fixture-' + run_id, status='running')
        with patch('agent_manager.interfaces.sdk.launch_worker', side_effect=accepted):
            return await client.spawn(profile='work', **options)

    async def test_settings_are_captured_by_idempotency_and_applied_to_fresh_and_resume(self):
        client = self.client()
        key = str(uuid.uuid4())
        settings = {'mcp_servers.example.command': 'example'}
        fresh = await self.launch(client, idempotency_key=key, native_config=settings)
        self.assertIn('mcp_servers.example.command="example"', read_run(self.manager, fresh.run_id)['launch']['command'])
        with self.assertRaisesRegex(Error, 'Idempotency'):
            await self.launch(client, idempotency_key=key, native_config={'mcp_servers.example.command': 'different'})
        native_id = str(uuid.uuid4())
        history = client.manager.home('work') / 'sessions'
        history.mkdir(exist_ok=True)
        import json
        (history / 'rollout.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {'id': native_id, 'cwd': str(self.base)}}) + '\n')
        resumed = await self.launch(client, thread_id=native_id, native_config=settings)
        command = read_run(self.manager, resumed.run_id)['launch']['command']
        self.assertEqual(command[-2:], ['resume', native_id])
        self.assertIn('mcp_servers.example.command="example"', command)

    async def test_observation_distinguishes_unknown_active_and_terminated_and_hides_launch_data(self):
        client = self.client()
        run = await self.launch(client)
        with patch('agent_manager.runtime.observation.observe', new=AsyncMock(return_value='running')):
            active = await client.execution_status(run.run_id)
        self.assertEqual(active['state'], 'active')
        self.assertNotIn('launch', active)
        with patch('agent_manager.runtime.observation.observe', new=AsyncMock(return_value='unknown')):
            self.assertEqual((await client.execution_status(run.run_id))['state'], 'unknown')
        finish_run(client.manager, run.run_id, status='finished')
        self.assertEqual((await client.execution_status(run.run_id))['state'], 'terminated')

    async def test_pending_ack_is_observed_by_exact_run_label_without_mutation(self):
        client = self.client()
        run = await self.launch(client)
        update_run(client.manager, run.run_id, status='submitting', session_id=None)
        before = client.manager.state_path.read_bytes()
        client.runtime.list = AsyncMock(return_value=[SimpleNamespace(id='observed', lifecycle='running')])
        observed = await client.execution_status(run.run_id)
        self.assertEqual(observed['state'], 'active')
        self.assertEqual(observed['session_id'], 'observed')
        client.runtime.list.assert_awaited_once_with(labels={'agent_manager.run_id': run.run_id})
        self.assertEqual(client.manager.state_path.read_bytes(), before)

    async def test_storage_redirects_are_rejected_before_any_process_request(self):
        client = self.client()
        with self.assertRaisesRegex(Error, 'profile-owned'):
            await client.spawn(profile='work', native_config={'"sqlite_home"': '/different-history'})
        self.assertFalse(client.manager.state()['runs'])

    async def test_live_app_server_attachment_rejects_process_config(self):
        client = self.client()
        with self.assertRaises(TypeError):
            await client.spawn(profile='work', app_server_run_id='server', thread_id=str(uuid.uuid4()), native_config={'model': 'x'})
