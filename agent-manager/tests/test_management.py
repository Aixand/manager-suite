"""Profile management preserves native data, stable IDs and revision guards."""
import hashlib
import json
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

from agent_manager import Error, Manager
from agent_manager.interfaces.management_contract import TOOLS, validate
from agent_manager.profiles.operations import Management, delete_profile, duplicate_profile, inspect_profile
from agent_manager.profiles.sessions import remember
from agent_manager.storage.files import atomic
from tests.support import ProfileFixture


class ManagementTests(ProfileFixture, unittest.TestCase):
    def setUp(self):
        super().setUp(); self.create()

    def test_disconnect_preserves_native_data_and_session_history(self):
        home = self.manager.home('work')
        marker = home / 'native-history'; marker.write_text('provider-owned')
        saved = remember(self.manager, 'work', str(uuid.uuid4()), cwd=self.base)
        result = delete_profile(self.manager, 'work')
        self.assertTrue(result['deleted']); self.assertTrue(result['native_data_preserved'])
        self.assertEqual(marker.read_text(), 'provider-owned')
        self.assertEqual(self.manager.state()['sessions'][saved['ref']], saved)
        self.assertFalse(self.manager.state()['profiles'])

    def test_registry_failure_preserves_profile_and_native_directory(self):
        before = self.manager.state(); home = self.manager.home('work')
        with patch.object(self.manager, 'save', side_effect=OSError('fixture write failure')):
            with self.assertRaises(OSError): delete_profile(self.manager, 'work')
        self.assertEqual(self.manager.state(), before)
        self.assertTrue(home.is_dir())

    def test_shared_native_home_is_never_purged(self):
        user = self.base / 'user'; user.mkdir()
        with patch.dict(os.environ, HOME=str(user), USERPROFILE=str(user)):
            manager = Manager('agy', config=self.config, root=self.base / 'shared')
            manager.new_profile('work'); home = manager.home('work')
            marker = home / 'native-history'; marker.write_text('keep')
            identifier = manager.resolve_profile('work').environment_id
            delete_profile(manager, 'work')
            with self.assertRaisesRegex(Error, 'owned'): manager.purge_environment(identifier)
            self.assertEqual(marker.read_text(), 'keep')

    def test_active_alias_can_be_disconnected_and_environment_remains_locked(self):
        target = self.manager.resolve_profile('work')
        state = self.manager.state(); state['runs']['live'] = {'agent': 'work', 'environment_id': target.environment_id, 'pid': os.getpid()}
        self.manager.save(state)
        delete_profile(self.manager, 'work')
        self.assertEqual(len(self.manager.active_runs(self.manager.state(), target)), 1)
        with self.assertRaisesRegex(Error, 'Active'): self.manager.forget_environment(target.environment_id)

    def test_duplicate_copies_preferences_without_native_history_or_login(self):
        identifier = self.manager.resolve_profile('work').environment_id
        state = self.manager.state(); state['models'] = {identifier: 'fixture/model'}
        for key in ('mcp_bindings', 'instruction_bindings', 'skill_bindings'):
            state[key] = {identifier: {'fixture': ['one']}}
        self.manager.save(state)
        home = self.manager.home('work'); (home / 'sessions').mkdir()
        copied = duplicate_profile(self.manager, 'work', 'copy')
        updated = self.manager.state()
        for key in ('models',):
            self.assertEqual(updated[key][copied['environment_id']], updated[key][identifier])
        self.assertNotEqual(copied['profile_id'], updated['profiles']['work']['id'])
        for name in ('sessions', 'native-login-marker'):
            self.assertFalse((Path(copied['home']) / name).exists())

    def test_revision_guards_and_identity_survive_rename(self):
        original = inspect_profile(self.manager, 'work')
        self.manager.rename('work', 'renamed', expected_revision=original['revision'])
        current = inspect_profile(self.manager, 'renamed')
        self.assertEqual(original['profile_id'], current['profile_id'])
        self.assertEqual(original['home'], current['home'])
        self.manager.rename('renamed', 'company')
        with self.assertRaisesRegex(Error, 'changed'):
            delete_profile(self.manager, 'company', expected_revision=current['revision'])

    def test_request_receipts_return_completed_results_and_never_replay_uncertain_intent(self):
        management = Management(self.manager); identifier = str(uuid.uuid4())
        values = {'name': 'new'}
        first = management.execute('create_profile', values, request_id=identifier)
        self.assertEqual(management.execute('create_profile', values, request_id=identifier), first)
        with self.assertRaisesRegex(Error, 'different arguments'):
            management.execute('create_profile', {'name': 'other'}, request_id=identifier)
        identifier = str(uuid.uuid4())
        signature = hashlib.sha256(json.dumps(['create_profile', dict(values, provider='test')], sort_keys=True).encode()).hexdigest()
        atomic(self.manager.root / 'profile-operations' / (identifier + '.json'),
            json.dumps({'status': 'submitting', 'signature': signature}).encode())
        with self.assertRaisesRegex(Error, 'completion is unconfirmed'):
            management.execute('create_profile', values, request_id=identifier)
        self.assertEqual(set(self.manager.state()['profiles']), {'work', 'new'})

    def test_contract_rejects_removed_tools_unknown_inputs_and_missing_revision(self):
        for tool, arguments in (('create_profile', {'provider': 'test', 'name': '../escape'}),
            ('create_profile', {'provider': 'test', 'name': 'new', 'password': 'private'}),
            ('delete_profile', {'provider': 'test', 'profile': 'work'}),
            ('switch_auth', {'provider': 'test', 'profile': 'work'})):
            with self.subTest(tool=tool), self.assertRaises(Error): validate(tool, arguments)
        self.assertNotIn('switch_auth', {row['name'] for row in TOOLS})

    def test_legacy_resource_inputs_are_private_and_do_not_change_core_revisions(self):
        state = self.manager.state()
        identifier = self.manager.resolve_profile('work').environment_id
        state['instruction_bindings'] = {identifier: {'reference': 'fixture@1/work', 'inputs': {'private': 'secret-one'}}}
        self.manager.save(state); before = inspect_profile(self.manager, 'work')
        self.assertNotIn('secret-one', json.dumps(before))
        state['instruction_bindings'][identifier]['inputs']['private'] = 'secret-two'; self.manager.save(state)
        self.assertEqual(inspect_profile(self.manager, 'work')['environment_revision'], before['environment_revision'])

    def test_reusing_a_disconnected_label_allocates_a_fresh_identity_and_home(self):
        before = inspect_profile(self.manager, 'work')
        delete_profile(self.manager, 'work'); after = self.manager.new_profile('work')
        self.assertNotEqual(before['profile_id'], after['profile_id'])
        self.assertNotEqual(before['home'], after['home'])
        self.assertTrue(Path(before['home']).is_dir())
