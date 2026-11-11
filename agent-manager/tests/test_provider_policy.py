"""Release defaults, compatibility boundaries and packaged policy parity."""
import unittest
from unittest.mock import patch

from agent_manager.providers import policy as policy


class ProviderPolicyTests(unittest.TestCase):
    def test_packaged_native_recommendations_are_supported_and_tested(self):
        for provider, row in policy.manifest()['providers'].items():
            with self.subTest(provider=provider):
                checked = policy.compatibility(provider, policy.recommended(provider))
                self.assertTrue(checked['compatible'])
                self.assertTrue(checked['tested'])

    def test_supported_stable_updates_do_not_need_to_be_in_the_tested_list(self):
        for provider, accepted, rejected in [
            ('codex', '0.166.1', '0.167.0'), ('opencode', '2.3.1', '3.0.0'),
            ('claude', '2.2.0', '3.0.0'), ('copilot', '1.1.0', '2.0.0'),
            ('antigravity', '1.5.0', '2.0.0'),
        ]:
            with self.subTest(provider=provider):
                self.assertTrue(policy.compatibility(provider, accepted)['compatible'])
                self.assertFalse(policy.compatibility(provider, accepted)['tested'])
                self.assertFalse(policy.compatibility(provider, rejected)['compatible'])
                self.assertFalse(policy.compatibility(provider, accepted + '-beta.1')['compatible'])

    def test_unknown_old_and_blocked_versions_are_rejected_including_build_variants(self):
        for value in [None, '', 'unknown', '2.0.17', '2.0', '2.2.0.1', '02.2.0']:
            self.assertFalse(policy.compatibility('opencode', value)['compatible'])
        self.assertTrue(policy.compatibility('opencode', '2.2.0+build.5')['compatible'])
        row = dict(policy.component('opencode'), blocked_versions=['2.2.1'])
        with patch.object(policy, 'component', return_value=row):
            for value in ['2.2.1', '2.2.1+vendor.3']:
                checked = policy.compatibility('opencode', value)
                self.assertFalse(checked['compatible'])
                self.assertIn('known incompatibility', checked['reason'])

    def test_cli_banners_are_parsed_without_accepting_version_prefixes(self):
        for banner, version in [('codex-cli 0.164.0', '0.164.0'),
                ('GitHub Copilot CLI 1.0.103.\nRun copilot update', '1.0.103'),
                ('opencode v2.2.0+build.1', '2.2.0+build.1')]:
            self.assertEqual(policy.cli_version(banner), version)
        for banner in ['tool 1.2', 'tool 1.2.3.4', 'tool 1.2.3bad']:
            self.assertIsNone(policy.cli_version(banner))

    def test_native_recommendation_and_artifact_policy(self):
        self.assertEqual(policy.recommended('claude'), '2.1.316')
        self.assertIn('known incompatibility', policy.compatibility('claude', '2.1.310')['reason'])
        for system in ['Linux', 'Darwin', 'Windows']:
            for arch in ['arm64', 'x86_64']:
                self.assertEqual(policy.artifact('antigravity', system, arch)['version'], '1.4.12')
        with self.assertRaisesRegex(ValueError, 'no verified'):
            policy.artifact('antigravity', 'Linux', 'x86_64', '1.4.13')
