"""Explicit Claude login selection never inherits another account's secrets."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from conversation_manager import ConversationError
from conversation_manager.adapters.claude_sdk import environment, terminal_error


class ClaudeLoginEnvironmentTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'macOS identity lookup uses pwd')
    def test_mac_profile_login_keeps_keychain_identity_while_scrubbing_secrets(self):
        import pwd
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary, patch('sys.platform', 'darwin'), \
                patch.object(pwd, 'getpwuid', return_value=SimpleNamespace(pw_name='mac-user')), \
                patch.dict(os.environ, USER='inherited-user', LOGNAME='inherited-login', ANTHROPIC_API_KEY='ambient-key'):
            selected = {'HOME': temporary, 'CLAUDE_CONFIG_DIR': str(Path(temporary) / 'account')}
            result = environment(selected, Path(temporary) / 'session', {'method': 'profile-login'})
        self.assertEqual(result['USER'], 'mac-user')
        self.assertEqual(result['LOGNAME'], 'mac-user')
        self.assertEqual(result['ANTHROPIC_API_KEY'], '')
        self.assertEqual(result['CLAUDE_CODE_OAUTH_TOKEN'], '')
        self.assertEqual(result['CLAUDE_CONFIG_DIR'], selected['CLAUDE_CONFIG_DIR'])

    def test_authentication_failure_is_actionable_without_echoing_diagnostics(self):
        from types import SimpleNamespace
        result = terminal_error(SimpleNamespace(is_error=True, result='Login expired · Please run /login fixture-token', errors=[]))
        self.assertEqual(result['code'], 'authentication')
        self.assertIn('original account', result['message'])
        self.assertNotIn('fixture-token', result['message'])
        self.assertIsNone(terminal_error(SimpleNamespace(is_error=False, result='Documentation about invalid API keys')))
    def test_profile_login_uses_only_the_selected_cli_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            selected = {"HOME": temporary, "CLAUDE_CONFIG_DIR": str(Path(temporary) / "work"),
                        "ANTHROPIC_API_KEY": "explicit-but-competing-key", "CLAUDE_CODE_OAUTH_TOKEN": "competing-token"}
            with patch.dict(os.environ, ANTHROPIC_API_KEY="ambient-key", ANTHROPIC_AUTH_TOKEN="ambient-token", CLAUDE_CONFIG_DIR="/other-account"):
                result = environment(selected, Path(temporary) / "session", {"method": "profile-login"})
            self.assertEqual(result["HOME"], temporary)
            self.assertEqual(result["CLAUDE_CONFIG_DIR"], selected["CLAUDE_CONFIG_DIR"])
            for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
                self.assertEqual(result[key], "")

    def test_default_login_and_api_key_modes_keep_separate_homes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = environment({"HOME": temporary, "CLAUDE_CONFIG_DIR": ""}, root / "session", {"method": "profile-login"})
            self.assertNotIn("CLAUDE_CONFIG_DIR", native)
            api = environment({"HOME": "/wrong-home", "CLAUDE_CONFIG_DIR": "/wrong-config", "ANTHROPIC_API_KEY": "selected-key"}, root)
            self.assertEqual(api["HOME"], temporary)
            self.assertEqual(api["CLAUDE_CONFIG_DIR"], str(root / "claude"))
            self.assertEqual(api["ANTHROPIC_API_KEY"], "selected-key")

    def test_login_mode_requires_an_explicit_absolute_configuration(self):
        for values in ({}, {"HOME": "/home/user"}, {"HOME": "relative", "CLAUDE_CONFIG_DIR": "/selected"},
                       {"HOME": "/home/user", "CLAUDE_CONFIG_DIR": "relative"}):
            with self.subTest(values=values), self.assertRaises(ConversationError):
                environment(values, Path("/session"), {"method": "profile-login"})

    def test_native_login_cannot_inherit_another_configuration_in_a_shared_process(self):
        with patch.dict(os.environ, CLAUDE_CONFIG_DIR="/another-account"), self.assertRaises(ConversationError):
            environment({"HOME": "/selected", "CLAUDE_CONFIG_DIR": ""}, Path("/session"), {"method": "profile-login"})
