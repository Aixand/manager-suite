"""Queued maintenance resolves an exact release without silently replacing tools."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_manager import Manager
from agent_manager.providers.installation import engine as install, plans as plans
from agent_manager.errors import Error


class InstallationPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = Manager('opencode', root=self.root)
        self.found = None
        self.problem = None
        for stub in [patch.object(plans.update, 'candidate', side_effect=lambda *a: SimpleNamespace(installation=self.found, error=self.problem)),
                     patch.object(install, 'prerequisites', return_value=None)]:
            stub.start()
            self.addCleanup(stub.stop)

    def installed(self, version, method='installer'):
        self.found = install.Installation(self.root / 'opencode', method, version, None)

    def test_fresh_install_freezes_default_and_rejects_a_changed_policy(self):
        plan = plans.resolve(self.manager, 'install', method='installer')
        self.assertEqual(plan['targetVersion'], '2.2.5')
        self.assertFalse(plan['keepExisting'])
        self.assertEqual(plans.validate(self.manager, plan), plan)
        with patch.object(plans, 'revision', return_value='new-release-policy'):
            with self.assertRaisesRegex(Error, 'changed'):
                plans.validate(self.manager, plan)
        with patch.object(plans.terminal_policy, 'revision', return_value='new-terminal-policy'):
            with self.assertRaisesRegex(Error, 'changed'):
                plans.validate(self.manager, plan)
        with patch.object(install.platform, 'machine', return_value='arm64'):
            with self.assertRaisesRegex(Error, 'changed'):
                plans.validate(self.manager, plan)
        self.assertFalse(self.manager.state_path.exists())

    def test_fresh_install_reenables_a_provider_after_uninstall(self):
        from agent_manager.providers.installation.software import state_path

        disabled = state_path(self.manager)
        disabled.parent.mkdir(parents=True, exist_ok=True)
        disabled.write_text('{"disabled": true}')
        plan = plans.resolve(self.manager, 'install', method='installer')
        with patch.object(install, 'perform', return_value=0):
            self.assertEqual(plans.execute(self.manager, plan), 0)
        self.assertFalse(disabled.exists())

    def test_newer_compatible_installation_is_kept_and_repair_preserves_its_version(self):
        self.installed('2.3.1')
        for kind in ['install', 'update']:
            plan = plans.resolve(self.manager, kind)
            self.assertTrue(plan['keepExisting'])
            self.assertEqual(plan['targetVersion'], '2.3.1')
        repair = plans.resolve(self.manager, 'repair')
        self.assertFalse(repair['keepExisting'])
        self.assertEqual(repair['targetVersion'], '2.3.1')
        restore = plans.resolve(self.manager, 'repair', version_target='recommended')
        self.assertEqual(restore['targetVersion'], '2.2.5')
        self.assertEqual(restore['versionTarget'], 'recommended')

    def test_incompatible_installation_requires_explicit_restore(self):
        self.installed('3.0.0')
        for kind in ['install', 'update', 'repair']:
            with self.assertRaisesRegex(Error, 'Restore recommended version'):
                plans.resolve(self.manager, kind)
        self.assertEqual(plans.resolve(self.manager, 'repair', version_target='recommended')['targetVersion'], '2.2.5')

    def test_changed_executable_version_invalidates_a_queued_plan(self):
        self.installed('2.1.4')
        plan = plans.resolve(self.manager, 'update')
        self.installed('2.3.1')
        with self.assertRaisesRegex(Error, 'changed'):
            plans.validate(self.manager, plan)

    def test_external_tools_can_be_kept_but_are_never_updated_or_repaired(self):
        for method in ('npm', 'brew', None):
            self.installed('2.3.1', method)
            self.problem = 'Externally installed CLI'
            keep = plans.resolve(self.manager, 'install')
            self.assertTrue(keep['keepExisting'])
            with patch.object(install, 'perform') as perform:
                self.assertEqual(plans.execute(self.manager, keep), 0)
            perform.assert_not_called()
            for kind in ('update', 'repair'):
                with self.assertRaisesRegex(Error, 'externally'):
                    plans.resolve(self.manager, kind)
        for method in ('npm', 'brew'):
            with self.assertRaisesRegex(Error, 'native installers'):
                plans.resolve(self.manager, 'install', method=method)

    def test_provider_sdk_installation_is_not_a_native_installation_operation(self):
        manager = Manager('claude', root=self.root)
        with self.assertRaisesRegex(Error, 'no CLI installation plan'):
            plans.resolve(manager, 'install-sdk')

    def test_explicit_restore_can_repair_a_broken_managed_cli_but_not_a_custom_location(self):
        self.found = install.Installation(self.root / 'opencode', 'installer', None, 'Version check failed')
        self.problem = self.found.error
        with patch.object(install, 'discover', return_value=[self.found]), \
                patch.object(install, 'destination', return_value=self.found.path):
            self.assertEqual(plans.resolve(self.manager, 'repair', version_target='recommended')['targetVersion'], '2.2.5')
        with patch.object(install, 'discover', return_value=[self.found]), \
                patch.object(install, 'destination', return_value=self.root / 'different-opencode'):
            with self.assertRaisesRegex(Error, 'outside the managed'):
                plans.resolve(self.manager, 'repair', version_target='recommended')
