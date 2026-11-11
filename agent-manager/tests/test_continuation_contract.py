"""Shared lifecycle guarantees using disposable native homes and history."""
import asyncio
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from agent_manager import Client, Error
from agent_manager.profiles.operations import delete_profile
from agent_manager.profiles.sessions import select, remember
from agent_manager.runtime.continuation import coordinator
from agent_manager.runtime.continuation.contracts import ContinuationPlan, ContinuationRequest
from agent_manager.runtime.execution import launch_home
from tests.support import ProfileFixture
from tests.test_opencode_continuation import CopyFixture


class SharedContractTests(ProfileFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.manager.new_profile('work')
        self.saved = remember(self.manager, 'work', 'native-session', cwd=self.base)

    def test_selection_is_immutable_and_does_not_write_registry_or_run_a_cli(self):
        before = self.manager.state_path.read_bytes()
        with patch('subprocess.run', side_effect=AssertionError('Selection must not run native commands')):
            request = select(self.manager, self.saved['ref'], 'work')
        self.assertIsInstance(request, ContinuationRequest)
        self.assertEqual(self.manager.state_path.read_bytes(), before)
        with self.assertRaises(FrozenInstanceError): request.home = self.base / 'another-home'
        with self.assertRaises(TypeError): request.source['native_id'] = 'another-session'
        plan = coordinator.prepare(self.manager, request)
        self.assertIsInstance(plan, ContinuationPlan)
        self.assertEqual(plan.native_id, 'native-session')
        self.assertEqual(plan.receipt['requested_session_ref'], plan.receipt['session_ref'])

    def test_captured_environment_survives_alias_rename_delete_and_name_reuse(self):
        request = select(self.manager, self.saved['ref'], 'work')
        self.manager.rename('work', 'company')
        delete_profile(self.manager, 'company')
        self.manager.new_profile('work')
        plan = coordinator.prepare(self.manager, request)
        self.assertEqual(plan.target.environment_id, request.target.environment_id)
        self.assertNotEqual(plan.target.environment_id, self.manager.resolve_profile('work').environment_id)
        self.assertEqual(plan.request.home, Path(self.saved['history_home']))

    def test_changed_source_or_destination_is_refused_before_adapter_preparation(self):
        request = select(self.manager, self.saved['ref'], 'work')
        adapter = coordinator.for_manager(self.manager)
        with patch.object(adapter, 'materialize', side_effect=AssertionError('No changes before validation')):
            with self.assertRaisesRegex(Error, 'home changed'):
                coordinator.prepare(self.manager, replace(request, home=self.base / 'changed'))
            state = self.manager.state()
            state['sessions'][self.saved['ref']]['cwd'] = str(self.base / 'changed')
            self.manager.save(state)
            with self.assertRaisesRegex(Error, 'reference changed'):
                coordinator.prepare(self.manager, request)

    def test_prepared_plan_cannot_be_attached_to_another_session(self):
        plan = coordinator.prepare(self.manager, select(self.manager, self.saved['ref'], 'work'))
        other = remember(self.manager, 'work', 'other-session', cwd=self.base)
        with self.assertRaisesRegex(Error, 'differs from the captured launch'):
            with launch_home(self.manager, 'work', cwd=self.base, thread_id=other['native_id'],
                             source_session=other, continuation_plan=plan):
                self.fail('A continuation plan was rebound to another session')
        changed = replace(plan, request=replace(plan.request, adapter_id='another-adapter'))
        with self.assertRaisesRegex(Error, 'differs from the captured launch'):
            with launch_home(self.manager, 'work', cwd=self.base, thread_id=plan.native_id,
                             source_session=dict(plan.session), continuation_plan=changed):
                self.fail('A continuation plan was rebound to another adapter')

    def test_queued_launch_revalidates_the_common_receipt(self):
        plan = coordinator.prepare(self.manager, select(self.manager, self.saved['ref'], 'work'))
        record = dict(continuation=plan.receipt, source_session_ref=self.saved['ref'],
                      source_history_home=self.saved['history_home'], origin_environment_id=self.saved['origin_environment_id'],
                      thread_id=plan.native_id, environment_id=plan.target.environment_id)
        coordinator.validate_submission(self.manager, record, 'fixture')
        for field, value in [('adapter', 'another-adapter'), ('environment_id', 'another-environment'),
                             ('native_id', 'another-session'), ('history_mode', 'copy')]:
            with self.subTest(field=field), self.assertRaisesRegex(Error, 'receipt changed'):
                coordinator.validate_submission(self.manager, dict(record, continuation=dict(plan.receipt, **{field: value})), 'fixture')


class CopyContractTests(CopyFixture, unittest.TestCase):
    def test_copy_selection_has_no_registry_or_native_history_side_effects(self):
        before = self.manager.state_path.read_bytes()
        with patch('subprocess.run', side_effect=AssertionError('Selection must not export or import')):
            request = select(self.manager, self.saved['ref'], 'target')
        self.assertEqual(self.manager.state_path.read_bytes(), before)
        self.assertEqual(self.imports('target'), [])
        plan = coordinator.prepare(self.manager, request)
        self.assertEqual(len(self.imports('target')), 1)
        self.assertEqual(plan.request.source['native_id'], self.sid)
        self.assertNotEqual(plan.native_id, self.sid)
        self.assertEqual(plan.receipt['origin_environment_id'], self.saved['origin_environment_id'])
        self.assertEqual(plan.session['origin_environment_id'], plan.target.environment_id)
        self.assertEqual(coordinator.prepare(self.manager, request).native_id, plan.native_id)
        self.assertEqual(len(self.imports('target')), 1)

    def test_copy_claim_has_no_shared_history_idle_gate(self):
        plan = coordinator.prepare(self.manager, select(self.manager, self.saved['ref'], 'target'))
        with patch('agent_manager.runtime.continuation.coordination.check_idle', side_effect=AssertionError('OpenCode does not require an idle source')):
            with launch_home(self.manager, plan.target, cwd=self.base, thread_id=plan.native_id,
                             source_session=dict(plan.session), continuation_plan=plan) as home:
                home.claim(self.manager, self.manager.state(), plan.target)
                self.assertEqual(home.provenance['continuation'], plan.receipt)

    def test_unavailable_original_does_not_block_a_current_available_copy(self):
        plan = coordinator.prepare(self.manager, select(self.manager, self.saved['ref'], 'target'))
        self.manager.home('source').rename(self.base / 'disconnected-original')
        continued = coordinator.prepare(self.manager, select(self.manager, plan.session['conversation_id'], 'third'))
        self.assertNotEqual(continued.native_id, plan.native_id)
        self.assertEqual(continued.session['conversation_id'], plan.session['conversation_id'])

    def test_invalid_sdk_attachment_is_refused_before_copy_preparation(self):
        client = object.__new__(Client)
        client.manager = self.manager
        client.spawn = AsyncMock()
        with self.assertRaisesRegex(Error, 'Unsupported native continuation option'):
            asyncio.run(client.continue_session(self.saved['ref'], profile='target', mode='app-server'))
        client.spawn.assert_not_called()
        self.assertEqual(self.imports('target'), [])
