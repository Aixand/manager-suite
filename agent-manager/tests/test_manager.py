"""Native launch and profile registry contracts using a disposable provider."""
import json
import os
import unittest
from unittest.mock import patch

from agent_manager import Error, Manager
from agent_manager.interfaces.cli.dispatch import execute
from tests.support import ProfileFixture


class ManagerTests(ProfileFixture, unittest.TestCase):
    def test_provider_preferences_apply_to_launches_without_changing_login_arguments(self):
        self.config.write_text(self.config.read_text() + '\nlaunch_args=["--model", "model with spaces"]\n')
        self.manager = Manager('test', self.config, self.base / 'homes')
        log = self.base / 'login.json'
        with patch.dict(os.environ, FAKE_LOGIN_LOG=str(log)):
            self.create(); self.create('other')
        self.assertEqual(json.loads(log.read_text()), ['login'])
        for profile in ('work', 'other'):
            self.assertEqual(self.manager._run_native(profile, ['--model', 'override']), 0)
            observed = json.loads((self.manager.home(profile) / 'observed.json').read_text())
            self.assertEqual(observed['args'], ['--configured', '--model', 'model with spaces', '--model', 'override'])
            self.assertEqual(observed['home'], str(self.manager.home(profile)))

    def test_codex_launch_does_not_force_a_credential_storage_mode(self):
        self.config.write_text('[providers.codex]\nlaunch_args=["--yolo"]\n')
        manager = Manager('codex', self.config, self.base / 'homes')
        manager.new_profile('work')
        with patch.object(manager, '_invoke_native', return_value=0) as invoke:
            manager._run_native('work', [])
        self.assertEqual(invoke.call_args.args[1], ['--no-daemon', '--yolo'])

    def test_invalid_launch_arguments_are_rejected_without_echoing_values(self):
        original = self.config.read_text()
        for value in ('"private-invalid-value"', '[1]', '["valid", false]'):
            with self.subTest(value=value):
                self.config.write_text(original + '\nlaunch_args=' + value + '\n')
                with self.assertRaisesRegex(Error, 'array of literal arguments') as caught:
                    Manager('test', self.config, self.base / 'homes')
                self.assertNotIn('private-invalid-value', str(caught.exception))

    def test_native_launch_settings_keep_provider_preferences(self):
        manager = Manager('codex', self.config, self.base / 'homes')
        manager.new_profile('work'); manager.config['launch_args'] = ['--yolo']
        with patch.object(manager, '_invoke_native', return_value=0) as invoke:
            manager._run_native('work', ['prompt'], native_config={'model_reasoning_effort': 'low'})
        self.assertEqual(invoke.call_args.args[1], ['--no-daemon', '--yolo', '-c', 'model_reasoning_effort="low"', 'prompt'])

    def test_no_implicit_profile_or_default_is_created_by_run(self):
        before = self.manager.state()
        for profile in (None, 'missing'):
            with self.assertRaises(Error): self.manager._run_native(profile, [])
        self.assertEqual(self.manager.state(), before)
        self.assertFalse(self.manager.state_path.exists())

    def test_native_exit_code_and_profile_files_are_preserved(self):
        self.create()
        marker = self.manager.home('work') / 'native-login-marker'
        before = marker.read_bytes()
        with patch.dict(os.environ, FAKE_RUN_EXIT='7'):
            self.assertEqual(self.manager._run_native('work', ['prompt']), 7)
        self.assertEqual(marker.read_bytes(), before)
        self.assertFalse(self.manager.state()['runs'])

    def test_login_forwards_native_arguments_unchanged(self):
        self.manager.new_profile('work')
        log = self.base / 'login.json'
        with patch.dict(os.environ, FAKE_LOGIN_LOG=str(log)):
            self.assertEqual(self.manager.login('work', ['--native-option', 'with spaces']), 0)
        self.assertEqual(json.loads(log.read_text()), ['login', '--native-option', 'with spaces'])

    def test_cli_rename_preserves_native_path_and_remains_runnable(self):
        self.create(); original = self.manager.home('work')
        identity = self.manager.state()['profiles']['work']['id']
        prefix = ['--config', str(self.config), '--home', str(self.base / 'homes'), 'test']
        self.assertEqual(execute([*prefix, 'profile', 'rename', 'work', 'renamed']), 0)
        self.assertEqual(self.manager.home('renamed'), original)
        self.assertEqual(self.manager.state()['profiles']['renamed']['id'], identity)
        self.assertEqual(execute([*prefix, 'run', 'renamed', '--', '--model', 'with spaces']), 0)
        self.assertEqual(json.loads((original / 'observed.json').read_text())['args'],
            ['--configured', '--model', 'with spaces'])

    def test_rename_rejects_invalid_unknown_and_occupied_labels_without_moving_files(self):
        self.create(); self.create('other')
        before = self.manager.state()
        for source, target in (('missing', 'new'), ('work', '../bad'), ('work', 'OTHER')):
            with self.subTest(source=source, target=target), self.assertRaises(Error):
                self.manager.rename(source, target)
        self.assertEqual(self.manager.state(), before)
        self.assertTrue((self.manager.home('work') / 'native-login-marker').is_file())

    def test_custom_provider_requires_a_manifest(self):
        self.config.write_text('[providers.unknown]\nexecutable="missing"\n')
        with self.assertRaisesRegex(Error, 'requires a manifest'):
            Manager('unknown', self.config, self.base / 'homes')
