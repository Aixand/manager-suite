import unittest
from unittest.mock import patch

from conversation_manager import native_startup_support


class NativeSupportTests(unittest.TestCase):
    def test_codex_native_startup_is_separate_from_account_readiness(self):
        for platform in ('linux', 'darwin', 'win32'):
            with self.subTest(platform=platform), patch('conversation_manager.support.sys.platform', platform):
                support = native_startup_support('codex')
                self.assertTrue(support['supported'])
                self.assertEqual(support['transport'], 'stdio')
                self.assertEqual(support['verification'], 'not-verified-with-account')

    def test_other_providers_do_not_inherit_codex_platform_support(self):
        with patch('conversation_manager.support.sys.platform', 'win32'):
            for provider in ('claude', 'unknown'):
                self.assertFalse(native_startup_support(provider)['supported'])

    def test_opencode_uses_private_http_on_supported_platforms(self):
        for platform in ('linux', 'darwin', 'win32'):
            with self.subTest(platform=platform), patch('conversation_manager.support.sys.platform', platform):
                support = native_startup_support('opencode')
                self.assertTrue(support['supported'])
                self.assertEqual(support['transport'], 'private-http')
        with patch('conversation_manager.support.sys.platform', 'unsupported'):
            self.assertFalse(native_startup_support('opencode')['supported'])
