"""Approved selectors resolve locally and use one CLI/maintenance install plan."""
import asyncio
import contextlib
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import patch

from agent_manager import Client, Error, Manager
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.providers import terminal_policy
from agent_manager.providers.installation import engine, releases, plans
from agent_manager.runtime import maintenance
from tests import test_agent_install as fixtures


class ReleaseCatalogTests(unittest.TestCase):
    def test_latest_is_the_default_while_continuation_stays_independent(self):
        with patch.object(engine.platform, 'system', return_value='Linux'), patch.object(engine.platform, 'machine', return_value='x86_64'), \
                patch('subprocess.run', side_effect=AssertionError('Catalog resolution must not run a CLI')):
            self.assertEqual(releases.resolve('opencode', 'latest'), '2.2.5')
            self.assertEqual(releases.resolve('opencode'), '2.2.5')
            self.assertEqual(releases.resolve('codex', 'v0.162.1'), '0.162.1')
            rows = {row['version']: row for row in releases.catalog('opencode')['versions']}
        self.assertTrue(rows['2.2.5']['latest'])
        self.assertTrue(rows['2.2.5']['recommended'])
        self.assertFalse(rows['2.2.5']['continuation']['available'])
        self.assertFalse(rows['2.0.18']['recommended'])
        self.assertTrue(rows['2.0.18']['continuation']['available'])
        self.assertEqual(rows['2.0.18']['continuation']['verification'], 'pending')

    def test_blocked_out_of_range_and_unknown_releases_are_not_selected(self):
        for provider, version, reason in [('claude', '2.1.310', 'known incompatibility'),
                                          ('codex', '0.167.0', 'supported range'),
                                          ('opencode', '2.3.1', 'not approved')]:
            with self.subTest(provider=provider), self.assertRaisesRegex(Error, reason):
                releases.resolve(provider, version)

    def test_latest_excludes_blocked_and_unavailable_artifacts(self):
        data = deepcopy(terminal_policy.manifest())
        data['providers']['claude']['install_versions'].append('2.1.310')
        data['providers']['antigravity']['install_versions'].append('1.4.13')
        with patch.object(terminal_policy, 'manifest', return_value=data), \
                patch.object(engine.platform, 'system', return_value='Linux'), patch.object(engine.platform, 'machine', return_value='x86_64'):
            self.assertEqual(releases.resolve('claude', 'latest'), '2.1.316')
            self.assertEqual(releases.resolve('antigravity', 'latest'), '1.4.12')
            rows = releases.catalog('antigravity')['versions']
            self.assertFalse(rows[0]['installable'])
            self.assertEqual(rows[0]['version'], '1.4.13')

    def test_platform_and_method_constraints_are_reported_without_installation(self):
        with patch.object(engine.platform, 'system', return_value='Windows'), patch.object(engine.platform, 'machine', return_value='AMD64'):
            for method in ('installer', 'npm'):
                result = releases.catalog('copilot', method=method)
                self.assertIsNone(result['latestVersion'])
                self.assertFalse(any(row['installable'] for row in result['versions']))
        with patch.object(engine.platform, 'system', return_value='FreeBSD'):
            self.assertIsNone(releases.catalog('opencode')['latestVersion'])
        for method in ('npm', 'brew'):
            with self.assertRaisesRegex(Error, 'native installers'):
                releases.resolve('codex', 'latest', method=method)

    def test_versions_cli_is_metadata_only_and_exposes_json(self):
        output = io.StringIO()
        with patch('subprocess.run', side_effect=AssertionError('Version listing must not run a provider')), \
                patch('agent_manager.interfaces.cli.dispatch.Manager', side_effect=AssertionError('No profile registry is needed')), \
                contextlib.redirect_stdout(output):
            self.assertEqual(execute(['versions', 'opencode', '--json']), 0)
        self.assertEqual(json.loads(output.getvalue())['recommendedVersion'], '2.2.5')


class InstallationSelectorTests(unittest.TestCase):
    setUp = fixtures.InstallTests.setUp
    go = fixtures.InstallTests.go
    receipt = fixtures.InstallTests.receipt
    calls = fixtures.InstallTests.calls
    writes = fixtures.InstallTests.writes
    seed_native = fixtures.InstallTests.seed_native

    def manager(self): return Manager('opencode', root=self.base / 'profiles')

    def test_latest_is_frozen_to_an_exact_release_and_recorded_in_the_receipt(self):
        self.assertEqual(self.go('opencode', '--installer', '--version', 'latest', '--yes'), 0)
        self.assertIn(['driver', 'opencode', '--version', '2.2.5', '--no-modify-path'], self.writes())
        record = json.loads(self.receipt('opencode').read_text())
        self.assertEqual(record['requestedSelector'], 'latest')
        self.assertEqual(record['resolvedVersion'], '2.2.5')
        self.assertIn('recipeRevision', record)
        self.assertIn('Cross-environment continuation: unavailable', self.stdout.getvalue())

    def test_changing_from_the_default_requires_reinstall_and_preserves_the_method(self):
        self.go('opencode', '--installer', '--version', 'latest', '--yes')
        with self.assertRaisesRegex(Error, '--reinstall'):
            self.go('opencode', '--installer', '--version', '2.0.18', '--yes')
        self.assertEqual(self.go('opencode', '--installer', '--version', '2.0.18', '--reinstall', '--yes'), 0)
        record = json.loads(self.receipt('opencode').read_text())
        self.assertEqual(record['version'], '2.0.18')
        self.assertEqual(record['previous_version'], '2.2.5')
        self.assertEqual(record['requestedSelector'], '2.0.18')

    def cached_copilot_downgrade(self, method):
        """A cached newer CLI must not masquerade as the installed old payload."""
        config = self.base / 'providers.toml'
        config.write_text('')
        probe = engine.output
        def cached(arguments):
            if arguments[0].endswith('/copilot') and arguments[1:] == ['--version']:
                return 'GitHub Copilot CLI 1.0.104.'
            return probe(arguments)
        with patch.dict(fixtures.os.environ, AGENT_MANAGER_CONFIG=str(config)), \
                patch.object(engine, 'output', side_effect=cached), contextlib.redirect_stdout(self.stdout):
            self.go('copilot', '--method', method, '--version', '1.0.104', '--yes')
            self.assertEqual(execute(['update', 'copilot', '--version', '1.0.103', '--yes']), 0)
            installed = engine.discover(engine.catalog()['copilot'])
            self.assertEqual(len(installed), 1)
            self.assertEqual((installed[0].version, installed[0].method), ('1.0.103', method))
            self.assertIn('1.0.104', engine.output([str(installed[0].path), '--version']))
            self.assertEqual(engine.inspect_external(Manager('copilot')).version, '1.0.103')
        receipt = json.loads(self.receipt('copilot').read_text())
        self.assertEqual((receipt['previous_version'], receipt['version'], receipt['requestedSelector']),
                         ('1.0.104', '1.0.103', '1.0.103'))


    def test_copilot_native_downgrade_checks_installed_binary_instead_of_cached_release(self):
        self.cached_copilot_downgrade('installer')

    def test_unapproved_or_blocked_explicit_versions_never_run_an_installer(self):
        for provider, version in [('claude', '2.1.310'), ('codex', '0.150.0'), ('opencode', '2.3.1')]:
            with self.subTest(provider=provider), self.assertRaises(Error):
                self.go(provider, '--installer', '--version', version)
        self.assertEqual(self.writes(), [])

    def test_cli_and_queued_maintenance_use_the_same_resolved_plan(self):
        manager = self.manager()
        expected = plans.resolve(manager, 'install', method='installer', version_target='latest')
        with patch.object(plans, 'execute', return_value=0) as install_plan:
            self.go('opencode', '--installer', '--version', 'latest', '--yes')
            self.assertEqual(install_plan.call_args.args[1], expected)
            result = maintenance.perform(dict(provider='opencode', config=manager.config_path,
                home=manager.root.parent, kind='install', method='installer', versionTarget='latest', installationPlan=expected))
            self.assertEqual(result['installationPlan'], expected)
            self.assertEqual(install_plan.call_args.args[1], expected)

    def test_policy_is_revalidated_under_the_installation_lock_before_download(self):
        manager = self.manager()
        plan = plans.resolve(manager, 'install', method='installer', version_target='latest')
        perform = engine.perform
        def changed_recipe(*args, **kwargs):
            with patch.object(releases, 'recipe_revision', return_value='changed-recipe'):
                return perform(*args, **kwargs)
        with patch.object(engine, 'perform', side_effect=changed_recipe), self.assertRaisesRegex(Error, 'changed'):
            plans.execute(manager, plan)
        self.assertEqual(self.writes(), [])

    def test_fresh_install_rechecks_a_changed_executable_override_before_download(self):
        config = self.base / 'providers.toml'
        config.write_text('')
        with patch.dict(fixtures.os.environ, AGENT_MANAGER_CONFIG=str(config)):
            manager = self.manager()
            plan = plans.resolve(manager, 'install', method='installer')
            perform = engine.perform
            def changed_override(*args, **kwargs):
                config.write_text('[providers.opencode]\nexecutable="/missing/changed-opencode"\n')
                return perform(*args, **kwargs)
            with patch.object(engine, 'perform', side_effect=changed_override), self.assertRaisesRegex(Error, 'not installed'):
                plans.execute(manager, plan)
        self.assertEqual(self.writes(), [])

    def test_sdk_exposes_the_same_metadata_and_plan(self):
        client = object.__new__(Client)
        client.manager = self.manager()
        catalog = asyncio.run(client.provider_versions())
        plan = asyncio.run(client.plan_provider_installation(version='latest', method='installer'))
        self.assertEqual(catalog['latestVersion'], plan['targetVersion'])
        self.assertEqual(plan, plans.resolve(client.manager, 'install', version_target='latest', method='installer'))
