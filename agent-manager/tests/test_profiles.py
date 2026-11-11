"""Providers without budget ranking retain explicit-default startup semantics."""
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr
from io import StringIO

from agent_manager import Error
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.profiles.selection import select_profile
from tests.support import ProfileFixture


class ProfileSelectionTests(ProfileFixture, unittest.TestCase):
    def test_default_is_required_even_with_one_available_profile(self):
        self.create()
        with self.assertRaisesRegex(Error, 'explicit default profile'):
            select_profile(self.manager)

    def test_selection_follows_the_explicit_default_and_rename(self):
        self.create(); self.create('other')
        self.manager.set_default('other')
        selected = select_profile(self.manager)
        self.assertEqual(selected['profile'], 'other')
        self.manager.rename('other', 'renamed')
        self.assertEqual(select_profile(self.manager)['profile_id'], selected['profile_id'])
        self.assertEqual(select_profile(self.manager)['profile'], 'renamed')

    def test_start_selects_default_and_run_uses_the_named_profile(self):
        self.create(); self.create('other'); self.manager.set_default('other')
        prefix = ['--config', str(self.config), '--home', str(self.base / 'homes'), 'test']
        with patch('agent_manager.manager.Manager._run_native', return_value=7) as run:
            self.assertEqual(execute([*prefix, 'start', '--', 'with spaces']), 7)
            self.assertEqual(run.call_args.args, ('other', ['with spaces']))
            self.assertEqual(execute([*prefix, 'run', 'work', '--', 'explicit prompt']), 7)
            self.assertEqual(run.call_args.args, ('work', ['explicit prompt']))

    def test_invalid_profile_forms_fail_before_launching_even_with_a_default(self):
        self.create(); self.manager.set_default('work')
        prefix = ['--config', str(self.config), '--home', str(self.base / 'homes'), 'test']
        with patch('agent_manager.manager.Manager._run_native') as run:
            for arguments in (['start', '--profile', 'work'], ['start', 'work'],
                              ['run'], ['run', '--profile', 'work']):
                with self.subTest(arguments=arguments), redirect_stderr(StringIO()), self.assertRaises(SystemExit) as caught:
                    execute([*prefix, *arguments])
                self.assertEqual(caught.exception.code, 2)
            run.assert_not_called()

    def test_selection_contains_profile_metadata_without_saved_auth_fields(self):
        self.create(); self.manager.set_default('work')
        selected = select_profile(self.manager)
        self.assertEqual(selected['home'], str(self.manager.home('work')))
        self.assertNotIn('auth', selected)
        self.assertNotIn('weekly_left', selected)
