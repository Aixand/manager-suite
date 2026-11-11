"""Execution handles, guarded auto startup, references and workspace discovery."""
import asyncio
from contextlib import aclosing
from dataclasses import asdict
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from agent_manager import Client, Error, ExecutionReference, SelectionDecision
from agent_manager.runtime.runs import read_run, update_run
from agent_manager.profiles.sessions import remember
from tests.support import ManagedFixture, FakeRuntime
from tests.test_budget_interfaces import BudgetFixture
from tests.test_automatic_selection import windows


class ExecutionHandleTests(ManagedFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.create()
        self.client_api = self.client()
        self.agent = await self.launch(self.client_api)
        self.runtime = self.client_api.runtime
        self.snapshot = SimpleNamespace(id=self.agent.session_id, lifecycle='running', execution='unknown',
            interaction='none', constraint='clear', health='healthy', revision=7)
        self.runtime.inspect = AsyncMock(return_value=self.snapshot)
        self.runtime.send_input = AsyncMock(return_value=SimpleNamespace(delivery_id='one', duplicate=False))
        self.runtime.send_key = AsyncMock(return_value=SimpleNamespace(delivery_id='key', duplicate=False))
        self.runtime.stop = AsyncMock(return_value=self.snapshot)
        self.runtime.wait = AsyncMock(return_value=self.snapshot)
        self.runtime.read_output = AsyncMock(return_value=SimpleNamespace(data=b'ab', from_offset=11, next_offset=100, has_more=True))
        self.runtime.read_screen = AsyncMock(return_value=b'\x1b[Hready')

    async def test_reference_roundtrip_and_dataclass_identity_do_not_serialize_client(self):
        reference = ExecutionReference.from_dict(json.loads(json.dumps(self.agent.reference.to_dict())))
        recovered = await self.client_api.get(reference.to_dict())
        self.assertEqual(recovered, self.agent)
        self.assertEqual(recovered.reference, reference)
        self.assertEqual(recovered.runtime_ref['id'], self.agent.session_id)
        self.assertNotIn('_client', asdict(recovered))
        self.assertNotIn('connection', reference.to_dict())

    async def test_state_preserves_independent_axes_without_inventing_completion(self):
        state = await self.agent.inspect()
        self.assertEqual(state.lifecycle, 'running')
        self.assertEqual(state.execution, 'unknown')
        self.assertEqual(state.interaction, 'none')
        self.assertIs(state.snapshot, self.snapshot)
        self.assertEqual(state.to_dict()['source'], 'undercurrent')
        self.assertNotIn('snapshot', state.to_dict())
        awaited = await self.agent.wait(interaction='available', timeout=9, after_revision=6)
        self.assertEqual(awaited.lifecycle, 'running')
        self.runtime.wait.assert_awaited_once_with(self.agent.session_id, interaction='available', timeout=9, after_revision=6)

    async def test_input_delivery_is_delegated_once_and_transport_errors_are_not_replayed(self):
        receipt = await self.agent.send_input('review', delivery_id='one', paste=True)
        self.assertEqual(receipt.delivery_id, 'one')
        self.runtime.send_input.assert_awaited_once_with(self.agent.session_id, 'review', submit=True,
            delivery_id='one', expected_revision=None, preserve_observation=False, paste=True)
        self.runtime.send_input.side_effect = TimeoutError('lost acknowledgment')
        with self.assertRaises(TimeoutError): await self.agent.send_input('uncertain', delivery_id='two')
        self.assertEqual(self.runtime.send_input.await_count, 2)
        await self.agent.send_key('escape', delivery_id='key')
        self.runtime.send_key.assert_awaited_once_with(self.agent.session_id, 'escape', delivery_id='key')

    async def test_output_pages_are_returned_without_changing_offsets_or_decoding_screen(self):
        page = await self.agent.read_output(from_offset=11, max_bytes=2)
        self.assertEqual(page.from_offset + len(page.data), 13)
        self.assertEqual(page.next_offset, 100)
        self.runtime.read_output.assert_awaited_once_with(self.agent.session_id, from_offset=11, max_bytes=2)
        self.assertEqual(await self.agent.read_screen(), b'\x1b[Hready')

    async def test_foreign_or_modified_references_cannot_target_input(self):
        for changes in ({'provider': 'other'}, {'registry_path': '/other/state.json'},
                        {'runtime_home': '/other/runtime'}, {'session_id': 'foreign'}):
            with self.subTest(changes=changes), self.assertRaises(Error):
                await self.client_api.get({**self.agent.reference.to_dict(), **changes})
        self.runtime.connection_options = {'home': '/wrong-runtime'}
        with self.assertRaisesRegex(Error, 'different Undercurrent home'):
            await self.agent.send_input('must not send')
        self.runtime.send_input.assert_not_called()

    async def test_stop_does_not_release_a_run_lease_without_terminal_evidence(self):
        result = await self.agent.stop(graceful_only=True, expected_revision=7)
        self.assertEqual(result.lifecycle, 'running')
        self.assertIn(self.agent.run_id, self.manager.state()['runs'])
        self.runtime.stop.assert_awaited_once_with(self.agent.session_id, force=False,
            graceful_only=True, expected_revision=7)

    async def test_watch_and_events_preserve_resync_gaps_and_close_underlying_streams(self):
        closed = []
        async def watch(target):
            self.assertEqual(target, self.agent.session_id)
            try:
                yield SimpleNamespace(session=self.snapshot, resynchronized=True)
                await asyncio.Future()
            finally: closed.append('watch')
        gap = {'item': 'gap', 'value': {'available_from': 9}}
        async def events(target, *, after_sequence):
            self.assertEqual(target, self.agent.session_id)
            self.assertEqual(after_sequence, 3)
            try:
                yield gap
                await asyncio.Future()
            finally: closed.append('events')
        self.runtime.watch, self.runtime.events = watch, events
        async with aclosing(self.agent.watch()) as stream:
            update = await anext(stream)
            self.assertTrue(update.resynchronized)
            self.assertEqual(update.state.revision, 7)
        async with aclosing(self.agent.events(after_sequence=3)) as stream:
            self.assertIs(await anext(stream), gap)
        self.assertEqual(closed, ['watch', 'events'])


    async def test_alias_deletion_does_not_retarget_the_execution_handle(self):
        from agent_manager.profiles.operations import delete_profile
        saved = self.agent.reference
        delete_profile(self.manager, 'work')
        recovered = await self.client_api.get(saved)
        self.assertEqual(recovered.reference, saved)
        await recovered.send_input('same execution')
        self.assertEqual(self.runtime.send_input.call_args.args[0], self.agent.session_id)


    async def test_provider_inventory_uses_this_clients_profiles_without_conversation_backends(self):
        def installation(manager):
            usable = manager.provider == 'test'
            return dict(provider=manager.provider, installed=usable, usable=usable,
                terminalUsable=usable, version=None, methods=[])
        with patch('agent_manager.providers.catalog.inspect_installation', side_effect=installation):
            inventory = await self.client_api.providers()
        self.assertEqual(inventory['schema'], 'agent-manager/provider-catalog-1')
        provider = next(row for row in inventory['providers'] if row['provider'] == 'test')
        self.assertEqual(provider['profiles'][0]['environment_id'], self.agent.environment_id)
        self.assertTrue(provider['profiles'][0]['readiness']['terminal']['ready'])
        self.assertNotIn('backends', provider)


class AutomaticSDKTests(BudgetFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.manager.new_profile('other')
        self.client_api = Client('codex', config=self.config, root=self.root, runtime=FakeRuntime())
        self.client_api.reconcile = AsyncMock(return_value=[])
        self.values = {'work': 80, 'other': 40}
        self.directories = []
        def read(manager, home, policy):
            self.directories.append(manager.working_directory())
            profile = next(name for name in self.values if manager.home(name) == home)
            return windows(self.values[profile])
        self.reader = self.enterContext(patch('agent_manager.budgets.adapters.read', side_effect=read))
        self.worker = self.enterContext(patch('agent_manager.interfaces.sdk.launch_worker', side_effect=self.accept))

    def accept(self, manager, run_id):
        update_run(manager, run_id, status='running', session_id='session-' + run_id)

    async def test_start_uses_highest_budget_in_requested_workspace_and_returns_evidence(self):
        default = self.manager.state()['default_profile_id']
        agent = await self.client_api.start(cwd=self.base)
        self.assertEqual(agent.profile, 'work')
        self.assertEqual(agent.selection.remaining_percent, 80)
        self.assertEqual(SelectionDecision.from_dict(json.loads(json.dumps(agent.selection.to_dict()))), agent.selection)
        self.assertEqual(agent.selection.reason, 'Highest remaining usable allowance')
        self.assertEqual(agent.selection.budget['provider'], 'codex')
        self.assertEqual(read_run(self.manager, agent.run_id)['launch']['cwd'], str(self.base))
        self.assertTrue(all(path == self.base for path in self.directories))
        self.assertEqual(self.manager.state()['default_profile_id'], default)

    async def test_retry_after_budget_changes_and_alias_rename_recovers_original_execution(self):
        key = str(uuid.uuid4())
        first = await self.client_api.start(cwd=self.base, idempotency_key=key)
        self.values.update(work=1, other=99)
        self.manager.rename('work', 'renamed')
        with patch('agent_manager.profiles.selection.choose', side_effect=AssertionError('must not rerank')):
            second = await self.client_api.start(cwd=self.base, idempotency_key=key)
        self.assertEqual(second.run_id, first.run_id)
        self.assertEqual(second.reference, first.reference)
        self.worker.assert_called_once()
        with self.assertRaisesRegex(Error, 'different automatic launch arguments'):
            await self.client_api.start(cwd=self.base, idempotency_key=key, args=['different'])

    async def test_concurrent_keyed_starts_select_and_dispatch_once(self):
        key = str(uuid.uuid4())
        first, second = await asyncio.gather(self.client_api.start(cwd=self.base, idempotency_key=key),
            self.client_api.start(cwd=self.base, idempotency_key=key))
        self.assertEqual(first, second)
        self.worker.assert_called_once()
        self.assertEqual(self.reader.call_count, 2)  # one observation for each environment

    async def test_selection_is_revalidated_before_dispatch(self):
        selected = self.client_api._selected_options
        def change(options, decision):
            self.manager.set_launch_arguments(['--changed-after-selection'], profile=decision.target)
            return selected(options, decision)
        with patch.object(self.client_api, '_selected_options', side_effect=change):
            with self.assertRaisesRegex(Error, 'environment changed'):
                await self.client_api.start(cwd=self.base)
        self.worker.assert_not_called()
        self.assertFalse(self.manager.state()['runs'])

    async def test_unqualified_allowance_requires_explicit_fallback_without_onboarding(self):
        self.values.update(work=None, other=None)
        with patch('agent_manager.interfaces.cli.onboarding.setup', side_effect=AssertionError('No SDK UI')):
            with self.assertRaisesRegex(Error, 'No codex profile has fresh usable allowance'):
                await self.client_api.start(cwd=self.base)
            agent = await self.client_api.start(cwd=self.base, fallback='default')
        self.assertEqual(agent.profile, 'work')
        self.assertIn('explicit fallback', agent.selection.reason)
        self.assertIsNone(agent.selection.budget)

    async def test_explicit_spawn_and_automatic_start_cannot_share_a_key(self):
        key = str(uuid.uuid4())
        await self.client_api.spawn(profile='work', cwd=self.base, idempotency_key=key)
        with self.assertRaisesRegex(Error, 'different managed launch'):
            await self.client_api.start(cwd=self.base, idempotency_key=key)

    async def test_folder_sessions_are_exact_by_default_and_recursive_only_when_requested(self):
        nested = self.base / 'nested'; nested.mkdir()
        elsewhere = self.base / 'elsewhere'; elsewhere.mkdir()
        remember(self.manager, 'work', str(uuid.uuid4()), cwd=self.base)
        remember(self.manager, 'work', str(uuid.uuid4()), cwd=nested)
        remember(self.manager, 'other', str(uuid.uuid4()), cwd=elsewhere)
        exact = await self.client_api.sessions(cwd=nested, refresh=False)
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0]['cwd'], str(nested))
        remember(self.manager, 'other', str(uuid.uuid4()), cwd=self.base.parent)
        rows = await self.client_api.sessions(cwd=self.base, recursive=True, refresh=False)
        self.assertEqual(len(rows), 3)
        with self.assertRaises(ValueError): await self.client_api.sessions(recursive=True)

    async def test_budget_refresh_between_selection_and_dispatch_invalidates_launch(self):
        selected = self.client_api._selected_options
        def refresh(options, decision):
            self.values['work'] = 5
            self.client_api.manager.budget(decision.target, refresh=True)
            return selected(options, decision)
        with patch.object(self.client_api, '_selected_options', side_effect=refresh):
            with self.assertRaisesRegex(Error, 'budget changed or expired'):
                await self.client_api.start(cwd=self.base)
        self.worker.assert_not_called()
        self.assertFalse(self.manager.state()['runs'])

    async def test_automatic_selection_journal_does_not_store_caller_environment_secrets(self):
        agent = await self.client_api.start(cwd=self.base, env={'PRIVATE_TEST_TOKEN': 'private-fixture-secret'})
        path = self.manager.root / 'automatic-starts' / (agent.run_id + '.json')
        self.assertNotIn('private-fixture-secret', path.read_text())
        self.assertNotIn('PRIVATE_TEST_TOKEN', path.read_text())

    async def test_invalid_automatic_options_are_rejected_before_budget_queries(self):
        for options in ({'model': False}, {'native_config': {'model': 5}}, {'profile': 'work'},
                        {'model': 'a', 'native_config': {'model': 'b'}}):
            with self.subTest(options=options), self.assertRaises((Error, ValueError)):
                await self.client_api.start(cwd=self.base, **options)
        self.reader.assert_not_called()
        self.worker.assert_not_called()

    async def test_quoted_native_model_setting_is_selected_and_retried_as_the_same_model(self):
        key = str(uuid.uuid4())
        first = await self.client_api.start(cwd=self.base, native_config={'"model"': 'gpt-test'}, idempotency_key=key)
        self.assertEqual(first.selection.model, 'gpt-test')
        second = await self.client_api.start(cwd=self.base, native_config={'model': 'gpt-test'}, idempotency_key=key)
        self.assertEqual(first.reference, second.reference)
        self.worker.assert_called_once()

    async def test_invalid_timeout_does_not_probe_accounts_or_prepare_execution(self):
        with self.assertRaises(ValueError): await self.client_api.start(cwd=self.base, timeout=0)
        self.reader.assert_not_called()
        self.worker.assert_not_called()

    async def test_missing_continuation_identity_cannot_recover_an_unrelated_fresh_start(self):
        key = str(uuid.uuid4())
        await self.client_api.start(cwd=self.base, idempotency_key=key)
        for session in (None, '', '   '):
            with self.subTest(session=session), self.assertRaises(ValueError):
                await self.client_api.continue_session(session, auto=True, idempotency_key=key)
        self.worker.assert_called_once()

    async def test_automatic_retry_recovers_accepted_run_after_workspace_is_removed(self):
        project = self.base / 'temporary-project'; project.mkdir()
        key = str(uuid.uuid4())
        original = await self.client_api.start(cwd=project, idempotency_key=key)
        project.rmdir()
        with patch('agent_manager.profiles.selection.choose', side_effect=AssertionError('No launch preflight on recovery')):
            recovered = await self.client_api.start(cwd=project, idempotency_key=key)
        self.assertEqual(recovered.reference, original.reference)
        self.worker.assert_called_once()

    async def test_fresh_automatic_launch_still_requires_an_existing_workspace(self):
        with self.assertRaisesRegex(Error, 'Working directory does not exist'):
            await self.client_api.start(cwd=self.base / 'missing-project')
        self.reader.assert_not_called()
        self.worker.assert_not_called()
        self.assertFalse(self.manager.state()['runs'])

    async def test_explicit_retry_ignores_transport_only_changes_but_not_work_or_daemon_changes(self):
        runtime = self.client_api.runtime
        home = runtime.connection_options['home']
        runtime.connection_options = dict(home=home, bridge='/old/bridge', request_timeout=10, request_bytes=10000)
        key = str(uuid.uuid4())
        original = await self.client_api.spawn(profile='work', cwd=self.base, idempotency_key=key)
        stored = read_run(self.manager, original.run_id)['request_signature']
        runtime.connection_options = dict(home=home, bridge='/new/bridge', request_timeout=30, request_bytes=20000)
        recovered = await self.client_api.spawn(profile='work', cwd=self.base, idempotency_key=key)
        self.assertEqual(recovered.reference, original.reference)
        self.assertEqual(read_run(self.manager, original.run_id)['request_signature'], stored)
        self.worker.assert_called_once()
        with self.assertRaisesRegex(Error, 'different launch arguments'):
            await self.client_api.spawn(profile='work', cwd=self.base, idempotency_key=key, env={'DIFFERENT': 'work'})
        runtime.connection_options = dict(home=home + '-different', request_timeout=30)
        with self.assertRaisesRegex(Error, 'different Undercurrent home'):
            await self.client_api.spawn(profile='work', cwd=self.base, idempotency_key=key)
        self.worker.assert_called_once()

    async def test_concurrent_explicit_retries_with_different_transports_dispatch_once(self):
        home = self.client_api.runtime.connection_options['home']
        other_runtime = FakeRuntime()
        other_runtime.connection_options = dict(home=home, bridge='/different/bridge', request_timeout=55)
        other = Client('codex', config=self.config, root=self.root, runtime=other_runtime)
        other.reconcile = AsyncMock(return_value=[])
        key = str(uuid.uuid4())
        first, second = await asyncio.gather(
            self.client_api.spawn(profile='work', cwd=self.base, idempotency_key=key),
            other.spawn(profile='work', cwd=self.base, idempotency_key=key))
        self.assertEqual(first.reference, second.reference)
        self.worker.assert_called_once()
