"""Shared profile behavior is driven by bundled provider definitions."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_manager import Manager
from agent_manager.profiles.operations import inspect_profile


class ProviderProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name); user = self.base / 'user'; user.mkdir()
        self.enterContext(patch.dict(os.environ, HOME=str(user), USERPROFILE=str(user)))
        self.config = self.base / 'providers.toml'
        self.config.write_text('\n'.join('[providers.' + provider + ']\nexecutable=' + json.dumps(sys.executable)
            for provider in ('codex', 'claude', 'copilot', 'opencode', 'antigravity')))

    def test_aliases_resolve_the_same_native_profile_registry(self):
        for alias, provider in (('agy', 'antigravity'), ('claude-code', 'claude'), ('copilot-cli', 'copilot')):
            with self.subTest(alias=alias):
                manager = Manager(alias, self.config, self.base / 'state'); original = manager.new_profile('work')
                canonical = Manager(provider, self.config, self.base / 'state')
                self.assertEqual(inspect_profile(canonical, 'work')['profile_id'], original['profile_id'])
                self.assertEqual(manager.provider_spec.settings['detection_profile'],
                    {'claude': 'claude-code', 'copilot': 'copilot-cli', 'antigravity': 'antigravity'}[provider])

    def test_each_provider_delegates_login_in_its_selected_home(self):
        for provider, arguments in (('codex', ['login']), ('opencode', ['auth', 'login', '--standalone']),
            ('claude', ['auth', 'login']), ('copilot', ['login']), ('antigravity', [])):
            with self.subTest(provider=provider):
                manager = Manager(provider, self.config, self.base / 'state'); manager.new_profile('work')
                with patch.object(manager, '_invoke_native', return_value=0) as invoke:
                    self.assertEqual(manager.login('work'), 0)
                self.assertEqual(invoke.call_args.args, (manager.home('work'), arguments))
                self.assertEqual(inspect_profile(manager, 'work')['login']['state'], 'unknown')

    def test_saved_auth_entry_points_are_absent(self):
        for provider in ('codex', 'claude', 'copilot', 'opencode', 'antigravity'):
            manager = Manager(provider, self.config, self.base / 'state')
            for method in ('new_auth', 'import_auth', 'read_auth', 'write_auth', 'switch', 'auth_home'):
                with self.subTest(provider=provider, method=method): self.assertFalse(hasattr(manager, method))
            self.assertFalse(manager.state_path.exists())
