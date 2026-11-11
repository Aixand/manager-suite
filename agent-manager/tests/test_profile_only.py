"""Profile-only contract coverage. No native CLI, SDK worker or model call is needed."""
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from agent_manager.errors import Error
from agent_manager.manager import Manager
from agent_manager.profiles.operations import delete_profile, duplicate_profile, inspect_profile, Management
from agent_manager.profiles.sessions import remember, listing, find, continue_session
from agent_manager.providers import load_definitions
from agent_manager.providers.schema import parse_definition, ManifestError


class ProfileOnlyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.config = self.base / 'providers.toml'
        self.config.write_text('')
        self.manager = Manager('codex', config=self.config, root=self.base / 'state')

    def test_profiles_have_independent_homes_and_no_implicit_default(self):
        work = self.manager.new_profile('work')
        backup = self.manager.new_profile('backup')
        self.assertNotEqual(work['profile_id'], backup['profile_id'])
        self.assertNotEqual(work['home'], backup['home'])
        self.assertNotEqual(Path(work['home']), self.manager.native)
        self.assertIsNone(self.manager.state()['default_profile_id'])
        with self.assertRaises(Error): self.manager.resolve_profile()
        self.manager.set_default('work')
        self.assertEqual(self.manager.resolve_profile(), 'work')
        self.assertNotIn('auths', self.manager.state())
        self.assertFalse(hasattr(self.manager, 'read_auth'))
        self.assertFalse(hasattr(self.manager, 'switch'))

    def test_rename_keeps_native_paths_and_session_references(self):
        original = self.manager.new_profile('work')
        home = self.manager.home('work')
        (home / 'native-marker').write_text('untouched')
        native_id = str(uuid.uuid4())
        saved = remember(self.manager, 'work', native_id, cwd=self.base)
        self.manager.set_default('work')
        self.manager.rename('work', 'renamed')
        renamed = inspect_profile(self.manager, 'renamed')
        self.assertEqual(renamed['profile_id'], original['profile_id'])
        self.assertEqual(self.manager.home('renamed'), home)
        self.assertEqual((home / 'native-marker').read_text(), 'untouched')
        self.assertEqual(self.manager.resolve_profile(), 'renamed')
        self.assertEqual(listing(self.manager, refresh=False)[0], saved)

    def test_disconnect_preserves_native_data_and_session_provenance(self):
        profile = self.manager.new_profile('work')
        home = self.manager.home('work')
        marker = home / 'native-credential-placeholder'
        marker.write_text('provider-owned')
        saved = remember(self.manager, 'work', str(uuid.uuid4()), cwd=self.base)
        delete_profile(self.manager, 'work')
        self.assertEqual(marker.read_text(), 'provider-owned')
        self.assertEqual(listing(self.manager, refresh=False)[0], saved)
        self.assertEqual(saved['origin_environment_id'], profile['environment_id'])

    def test_purge_refuses_referenced_or_external_homes(self):
        self.manager.new_profile('work')
        home = self.manager.home('work')
        remember(self.manager, 'work', str(uuid.uuid4()), cwd=self.base)
        identifier = self.manager.resolve_profile('work').environment_id
        delete_profile(self.manager, 'work')
        with self.assertRaisesRegex(Error, 'Retained sessions'): self.manager.purge_environment(identifier)
        self.assertTrue(home.is_dir())
        external = self.base / 'external'; external.mkdir()
        self.manager.connect_profile('external', external)
        identifier = self.manager.resolve_profile('external').environment_id
        delete_profile(self.manager, 'external')
        with self.assertRaisesRegex(Error, 'owned'): self.manager.purge_environment(identifier)
        self.assertTrue(external.is_dir())

    def test_duplicate_does_not_copy_native_credentials_or_history(self):
        self.manager.new_profile('work')
        home = self.manager.home('work')
        (home / 'native-credential-placeholder').write_text('provider-owned')
        (home / 'sessions').mkdir()
        (home / 'sessions' / 'transcript').write_text('provider-owned history')
        duplicate_profile(self.manager, 'work', 'copy')
        copied = self.manager.home('copy')
        self.assertFalse((copied / 'native-credential-placeholder').exists())
        self.assertFalse((copied / 'sessions').exists())

    def test_login_calls_native_cli_in_place_without_credential_publication(self):
        self.manager.new_profile('work')
        home = self.manager.home('work')
        marker = home / 'native-credential-placeholder'; marker.write_text('leave to provider')
        result = SimpleNamespace(returncode=0, stdout='signed-in', stderr='')
        with patch.object(self.manager, '_invoke_native', return_value=0) as native, \
             patch('agent_manager.environments.login.subprocess.run', return_value=result):
            self.assertEqual(self.manager.login('work'), 0)
        self.assertEqual(native.call_args.args, (home, ['login']))
        self.assertEqual(marker.read_text(), 'leave to provider')
        self.assertNotIn('leave to provider', self.manager.state_path.read_text())
        self.assertEqual(inspect_profile(self.manager, 'work')['login']['state'], 'signed-in')

    def test_codex_logged_out_is_distinct_from_failed_status_probe(self):
        self.manager.new_profile('work')
        for output, expected in [('Not logged in', 'signed-out'), ('Unable to reach service', 'unknown')]:
            with patch('agent_manager.environments.login.subprocess.run',
                    return_value=SimpleNamespace(returncode=1, stdout='', stderr=output)):
                self.assertEqual(self.manager.login_status('work')['state'], expected)

    def test_status_does_not_persist_raw_provider_output(self):
        self.manager.new_profile('work')
        result = SimpleNamespace(returncode=0, stdout='opaque-native-output', stderr='sensitive-placeholder')
        with patch('agent_manager.environments.login.subprocess.run', return_value=result):
            status = self.manager.login_status('work')
        self.assertEqual(status['state'], 'signed-in')
        self.assertNotIn('opaque-native-output', self.manager.state_path.read_text())
        self.assertNotIn('sensitive-placeholder', self.manager.state_path.read_text())

    def test_cross_home_continuation_refuses_before_launch(self):
        self.manager.new_profile('work'); self.manager.new_profile('backup')
        saved = remember(self.manager, 'work', str(uuid.uuid4()), cwd=Path.cwd())
        disabled = replace(self.manager.provider_spec, sessions=replace(self.manager.provider_spec.sessions, cross_environment_resume=False))
        with patch.object(self.manager, 'provider_spec', disabled), patch.object(self.manager, '_run_native') as launch:
            with self.assertRaisesRegex(Error, 'Cross-environment'):
                continue_session(self.manager, saved['ref'], profile='backup')
        launch.assert_not_called()

    def test_continue_supplies_saved_workspace_without_changing_process_cwd(self):
        self.manager.new_profile('work')
        native_id = str(uuid.uuid4())
        sessions = self.manager.home('work') / 'sessions'; sessions.mkdir()
        (sessions / 'rollout.jsonl').write_text(json.dumps({'type': 'session_meta',
            'payload': {'id': native_id, 'cwd': str(self.base)}}) + '\n')
        saved = remember(self.manager, 'work', native_id, cwd=self.base)
        before = Path.cwd()
        with patch.object(self.manager, '_run_native', return_value=0) as launch:
            self.assertEqual(continue_session(self.manager, saved['ref'], profile='work'), 0)
        self.assertEqual(launch.call_args.kwargs['cwd'], str(self.base))
        self.assertEqual(Path.cwd(), before)

    def test_duplicate_native_ids_require_exact_session_reference(self):
        self.manager.new_profile('work'); self.manager.new_profile('backup')
        native_id = str(uuid.uuid4())
        first = remember(self.manager, 'work', native_id, cwd=self.base)
        remember(self.manager, 'backup', native_id, cwd=self.base)
        with self.assertRaisesRegex(Error, 'several native homes'): find(self.manager, native_id)
        self.assertEqual(find(self.manager, first['ref']), first)

    def test_management_receipt_does_not_replay_a_completed_mutation(self):
        management = Management(self.manager)
        identifier = str(uuid.uuid4())
        first = management.execute('create_profile', {'name': 'work'}, request_id=identifier)
        second = management.execute('create_profile', {'name': 'work'}, request_id=identifier)
        self.assertEqual(first, second)
        self.assertEqual(list(self.manager.state()['profiles']), ['work'])
        with self.assertRaisesRegex(Error, 'different arguments'):
            management.execute('create_profile', {'name': 'other'}, request_id=identifier)

    def test_old_registry_is_rejected_without_rewriting_native_files(self):
        self.manager.root.mkdir(parents=True)
        self.manager.state_path.write_text(json.dumps({'agents': {'default': None}, 'auths': []}))
        original = self.manager.state_path.read_bytes()
        with self.assertRaisesRegex(Error, 'fresh version-three'): self.manager.state()
        self.assertEqual(self.manager.state_path.read_bytes(), original)

    def test_all_bundled_manifests_declare_only_native_profile_login(self):
        for definition in load_definitions(config=self.config).values():
            self.assertNotIn('auth', definition.terminal)
            self.assertIn('login', definition.terminal)
            expected = {'codex': 'codex-linux-history', 'claude': 'claude-transcript-path', 'copilot': 'copilot-session-link', 'opencode': 'opencode-session-copy'}.get(definition.id, 'disabled')
            self.assertEqual(definition.terminal['sessions']['cross_environment_resume'], expected)
        with self.assertRaises(ManifestError):
            parse_definition({'schema_version': 1})
