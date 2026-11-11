"""Setup, interactive startup recovery and public CLI presentation."""
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from agent_manager.errors import Error
from agent_manager.interfaces.cli import onboarding
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.interfaces.cli.start import start
from agent_manager.profiles.selection import SelectionUnavailable
from tests.support import ProfileFixture


class OnboardingTests(ProfileFixture, unittest.TestCase):
    def terminal(self):
        self.enterContext(patch('sys.stdin.isatty', return_value=True))
        self.enterContext(patch('sys.stdout.isatty', return_value=True))

    def test_setup_creates_default_profile_and_cancellation_creates_nothing(self):
        self.terminal()
        self.assertIsNone(onboarding.setup(self.manager, picker=lambda *a, **k: None))
        self.assertFalse(self.manager.state()['profiles'])
        target = onboarding.setup(self.manager, picker=lambda *a, **k: 0, prompt=lambda *a, **k: 'new')
        self.assertEqual(str(target), 'new')
        self.assertEqual(self.manager.resolve_profile().profile_id, target.profile_id)

    def test_setup_invalid_name_can_retry(self):
        self.terminal()
        names = iter(['invalid/name', 'valid'])
        target = onboarding.setup(self.manager, picker=lambda *a, **k: 0,
            prompt=lambda *a, **k: next(names))
        self.assertEqual(str(target), 'valid')

    def test_existing_profiles_are_selected_without_new_profile(self):
        self.terminal()
        self.manager.new_profile('first'); self.manager.new_profile('second')
        selections = iter([0, 1])
        target = onboarding.setup(self.manager, picker=lambda *a, **k: next(selections))
        self.assertEqual(str(target), 'second')
        self.assertEqual(len(self.manager.state()['profiles']), 2)

    def test_signed_in_authentication_never_prompts(self):
        self.manager.new_profile('work')
        with patch.object(self.manager, 'login_status', return_value={'state': 'signed-in'}):
            self.assertTrue(onboarding.authenticate(self.manager, self.manager.resolve_profile('work'),
                picker=lambda *a: self.fail('Unexpected prompt')))

    def test_connect_existing_native_home(self):
        self.terminal()
        directory = self.base / 'existing'; directory.mkdir()
        marker = directory / 'native-marker'; marker.write_text('keep')
        values = iter(['connected', str(directory)])
        target = onboarding.setup(self.manager, picker=lambda *a, **k: 1,
            prompt=lambda *a, **k: next(values))
        self.assertEqual(self.manager.home(target), directory)
        self.assertEqual(marker.read_text(), 'keep')

    def test_installer_cancellation_does_not_create_a_profile(self):
        self.terminal()
        with patch.object(self.manager, 'provider', 'codex'), patch.object(onboarding.shutil, 'which', return_value=None), \
             patch('agent_manager.providers.installation.engine.command', return_value=0) as install:
            self.assertIsNone(onboarding.setup(self.manager, picker=lambda *a, **k: None))
            install.assert_not_called()
        self.assertFalse(self.manager.state()['profiles'])

    def test_setup_missing_cli_reuses_installer_and_continues(self):
        self.terminal()
        with patch.object(self.manager, 'provider', 'codex'), patch.object(onboarding.shutil, 'which', side_effect=[None, str(self.fake)]), \
             patch('agent_manager.providers.installation.engine.command', return_value=0) as install:
            target = onboarding.setup(self.manager, picker=lambda *a, **k: 0, prompt=lambda *a, **k: 'main')
        self.assertEqual(str(target), 'main')
        self.assertEqual(install.call_args.args[0].target, 'codex')
        self.assertEqual(install.call_args.kwargs['home'], self.manager.root.parent)

    def test_start_without_profiles_uses_setup_and_preserves_launch_options(self):
        self.terminal()
        with patch.object(onboarding, 'setup', side_effect=lambda m: m.resolve_profile(m.new_profile('new')['profile'])), \
             patch.object(onboarding, 'authenticate', return_value=True), patch.object(self.manager, '_run_native', return_value=7) as run:
            self.assertEqual(start(self.manager, ['--native'], model='example'), 7)
        self.assertEqual(str(run.call_args.args[0]), 'new')
        self.assertEqual(run.call_args.args[1], ['--native'])
        self.assertEqual(run.call_args.kwargs['model'], 'example')

    def test_unknown_budget_can_launch_explicit_profile_but_other_errors_propagate(self):
        self.terminal(); self.manager.new_profile('work')
        with patch('agent_manager.profiles.selection.choose', side_effect=SelectionUnavailable('unavailable')), \
             patch.object(onboarding, 'authenticate', return_value=True), patch.object(self.manager, '_run_native', return_value=0) as run:
            self.assertEqual(start(self.manager, []), 0)
            run.assert_called_once()
        with patch('agent_manager.profiles.selection.choose', side_effect=Error('changed')):
            with self.assertRaisesRegex(Error, 'changed'): start(self.manager, [])

    def test_noninteractive_incomplete_setup_is_actionable(self):
        with patch('sys.stdin.isatty', return_value=False):
            with self.assertRaisesRegex(Error, 'onboarding'): start(self.manager, [])

    def test_authentication_cancel_failure_and_unknown_status_launch(self):
        self.manager.new_profile('work'); target = self.manager.resolve_profile('work')
        with patch.object(self.manager, 'login_status', return_value={'state': 'unknown'}), \
             patch.object(self.manager, 'login', return_value=1) as login:
            self.assertFalse(onboarding.authenticate(self.manager, target, picker=lambda *a: None))
            self.assertTrue(onboarding.authenticate(self.manager, target, picker=lambda *a: 1))
            login.assert_not_called()
            with self.assertRaisesRegex(Error, 'did not complete'):
                onboarding.authenticate(self.manager, target, picker=lambda *a: 0)

    def invoke(self, *arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            execute(['--config', str(self.config), '--home', str(self.base / 'homes'), 'test', *arguments])
        return output.getvalue()

    def test_profile_output_defaults_to_text_and_json_remains_explicit(self):
        self.assertIn('Created test profile "work"', self.invoke('profile', 'new', 'work'))
        self.assertIn('Profile', self.invoke('profile', 'list'))
        self.assertEqual(json.loads(self.invoke('profile', 'list', '--json'))[0]['profile'], 'work')
        self.assertEqual(json.loads(self.invoke('profile', 'new', 'other', '--json'))['profile'], 'other')

    def test_global_onboarding_routes_before_provider_resolution(self):
        with patch.object(onboarding, 'global_command', return_value=0) as command:
            self.assertEqual(execute(['onboarding']), 0)
            command.assert_called_once_with(config=None, home=None)


if __name__ == '__main__': unittest.main()
