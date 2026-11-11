"""Feature qualification agrees with installation and observed capabilities."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from agent_manager import Manager
from agent_manager.providers import policy, terminal_policy
from agent_manager.providers.catalog import inspect_provider, inspect_installation
from agent_manager.providers.installation import engine


class TerminalPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'providers.toml'
        self.config.write_text('')

    def manager(self, provider):
        return Manager(provider, config=self.config, root=self.root / provider)

    def test_recommendations_are_the_latest_approved_releases(self):
        targets = dict(codex='0.166.0', opencode='2.2.5', claude='2.1.316', copilot='1.0.105', antigravity='1.4.12')
        for provider, version in targets.items():
            with self.subTest(provider=provider):
                self.assertEqual(terminal_policy.recommended(provider), version)
                self.assertEqual(engine.catalog()[provider]['recommended_version'], version)
                self.assertTrue(policy.compatibility(provider, version)['compatible'])
                if provider not in ('antigravity', 'opencode'):
                    self.assertTrue(terminal_policy.supports_continuation(provider, version))
                with patch.object(engine, 'prerequisites', return_value=None):
                    guide = engine.setup_guide(provider)
                self.assertEqual(guide['recommendedVersion'], version)
                for method in guide['methods']:
                    if method['id'] != 'brew': self.assertIn(version, method['command'])

    def test_general_compatibility_remains_broader_than_history_support(self):
        for provider, version in [('opencode', '2.2.5'), ('copilot', '1.0.104'), ('claude', '2.1.303'), ('codex', '0.167.0')]:
            with self.subTest(provider=provider):
                if provider != 'codex': self.assertTrue(terminal_policy.compatibility(provider, version)['compatible'])
                result = terminal_policy.continuation(provider, version, checked=True)
                self.assertFalse(result['compatible'])
                self.assertEqual(result['status'], 'incompatible')
                target = terminal_policy.requirements(provider)['recommendedVersion']
                self.assertIn(f'agent install {provider} --reinstall --version {target} --yes', result['reason'])
        for version in [None, '', 'unknown', '2.0.18-beta.1']:
            self.assertFalse(terminal_policy.supports_continuation('opencode', version))
        self.assertFalse(terminal_policy.supports_continuation('claude', '2.1.310+vendor.1'))
        for provider in ('claude', 'copilot', 'opencode'):
            self.assertFalse(terminal_policy.supports_continuation(provider, terminal_policy.recommended(provider) + '+vendor.1'))
        self.assertTrue(terminal_policy.supports_continuation('codex', '0.164.0+vendor.1'))
        self.assertTrue(terminal_policy.supports_continuation('codex', '0.166.0'))
        self.assertFalse(terminal_policy.supports_continuation('antigravity', '1.4.12'))

    def test_static_capabilities_do_not_probe_and_report_unchecked_versions(self):
        manager = self.manager('opencode')
        with patch('subprocess.run', side_effect=AssertionError('metadata must not run a CLI')):
            caps = manager.provider_spec.capabilities()
        self.assertTrue(caps['crossEnvironmentResume'])
        feature = caps['crossEnvironmentResumeCompatibility']
        self.assertEqual(feature['status'], 'unchecked')
        self.assertIsNone(feature['compatible'])
        self.assertEqual(feature['supportedRange'], '==2.0.18')
        self.assertEqual(feature['verification'], 'pending')
        self.assertIn('overwrote an existing session', feature['verificationNotes'])
        self.assertFalse(manager.state_path.exists())

    def test_observed_inventory_disables_only_the_unsupported_continuation_feature(self):
        manager = self.manager('opencode')
        manager.new_profile('work')
        info = dict(installed=True, usable=True, terminalUsable=True, version='2.2.0')
        with patch('agent_manager.providers.catalog.inspect_installation', return_value=info), \
                patch('subprocess.run', side_effect=AssertionError('reuse the existing installation observation')):
            result = inspect_provider(manager)
        self.assertTrue(result['usable'])
        self.assertFalse(result['capabilities']['crossEnvironmentResume'])
        self.assertEqual(result['capabilities']['crossEnvironmentResumeCompatibility']['status'], 'incompatible')
        self.assertIn('2.0.18', result['capabilities']['crossEnvironmentResumeReason'])
        self.assertFalse(result['profiles'][0]['capabilities']['crossEnvironmentResume'])
        self.assertTrue(result['profiles'][0]['readiness']['terminal']['ready'])
        for version, status, enabled in [('2.0.18', 'compatible', True), (None, 'missing', False)]:
            caps = manager.provider_spec.capabilities(version=version, version_checked=True)
            self.assertEqual(caps['crossEnvironmentResume'], enabled)
            self.assertEqual(caps['crossEnvironmentResumeCompatibility']['status'], status)

    def test_inventory_recommendation_matches_its_setup_and_compatibility(self):
        manager = self.manager('opencode')
        found = engine.Installation(self.root / 'opencode', 'npm', '2.2.0', None)
        with patch('agent_manager.providers.catalog.update.candidate', return_value=SimpleNamespace(installation=found, error=None)), \
                patch.object(engine, 'prerequisites', return_value=None), \
                patch('agent_manager.providers.installation.exports.status', return_value={}):
            result = inspect_installation(manager)
        self.assertEqual(result['recommendedVersion'], '2.2.5')
        self.assertEqual(result['setup']['recommendedVersion'], '2.2.5')
        self.assertEqual(result['compatibility']['recommendedVersion'], '2.2.5')
        self.assertTrue(result['compatibility']['compatible'])

    def test_platform_restrictions_still_apply_with_a_qualified_version(self):
        with patch('agent_manager.runtime.native.file_continuation.sys.platform', 'win32'):
            manager = self.manager('claude')
        caps = manager.provider_spec.capabilities(version='2.1.316', version_checked=True)
        self.assertFalse(caps['crossEnvironmentResume'])
        self.assertIn('Linux', caps['crossEnvironmentResumeReason'])
        self.assertTrue(caps['crossEnvironmentResumeCompatibility']['compatible'])

    def test_adapters_use_the_same_feature_policy_as_inventory(self):
        from agent_manager.runtime.native import file_continuation, codex_continuation, opencode_transfer
        probes = [('claude', file_continuation._version, lambda provider: (provider, '/fixture/cli', 1, 1)),
                  ('copilot', file_continuation._version, lambda provider: (provider, '/fixture/cli', 1, 1)),
                  ('opencode', opencode_transfer._version, lambda provider: ('/fixture/cli', 1, 1)),
                  ('codex', codex_continuation._version, lambda provider: ('/fixture/cli', 1, 1))]
        for provider, probe, args in probes:
            qualified = terminal_policy.requirements(provider)['recommendedVersion']
            for version in [qualified, qualified + '+vendor.1', '9.9.9', 'unknown']:
                with self.subTest(provider=provider, version=version):
                    probe.cache_clear()
                    self.addCleanup(probe.cache_clear)
                    observed = SimpleNamespace(returncode=0, stdout=version)
                    with patch('subprocess.run', return_value=observed):
                        self.assertEqual(probe(*args(provider)), terminal_policy.supports_continuation(provider, version))
