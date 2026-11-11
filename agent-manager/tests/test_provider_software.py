"""Destructive provider operations exercise real files in private homes only."""
import json
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_manager import Manager
from agent_manager.providers.installation import engine as install
from agent_manager.errors import Error
from agent_manager.providers.installation import software as software


class ProviderSoftwareTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.manager = Manager('claude', root=self.home / 'manager')
        self.path = self.home / '.local/bin/claude'
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'fake CLI')
        self.found = install.Installation(self.path, 'installer', '2.1.302', None)
        for stub in [patch('pathlib.Path.home', return_value=self.home),
                     patch('platform.system', return_value='Linux'),
                     patch('agent_manager.providers.installation.engine.manager_prefix', return_value=None),
                     patch('agent_manager.providers.installation.engine.provider_executable', side_effect=lambda manager: self.path if self.path.exists() else None),
                     patch('agent_manager.providers.installation.engine.inspect', side_effect=lambda spec, path: self.found),
                     patch('agent_manager.providers.installation.engine.state_root', return_value=self.home / 'state'),
                     patch('agent_manager.providers.catalog.active_profiles', return_value=[]),
                     patch.dict(os.environ, AIXAND_PROVIDER_BUNDLE_ROOT='')]:
            stub.start()
            self.addCleanup(stub.stop)

    def perform(self, kind, revision=None):
        return software.perform(self.manager, kind, revision=revision or software.plan(self.manager)['revision'], confirmed=True)

    def test_native_uninstall_preserves_profile_homes_projects_and_sdk_files(self):
        versions = self.home / '.local/share/claude/versions'
        versions.mkdir(parents=True)
        (versions / '2.1.302').write_bytes(b'vendor executable')
        self.manager.new_profile('work')
        preserved = [self.home / '.claude/native-login-marker', self.manager.home('work') / 'native-login-marker',
                     self.home / 'project/README.md', self.home / 'sdk/package.json']
        for path in preserved:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'keep me')
        result = self.perform('uninstall')
        self.assertTrue(result['uninstalled'])
        self.assertFalse(self.path.exists())
        self.assertFalse(versions.exists())
        self.assertTrue(software.disabled(self.manager))
        for path in preserved:
            self.assertEqual(path.read_bytes(), b'keep me')

    def test_codex_uninstall_removes_standalone_payload_but_preserves_native_home(self):
        manager = Manager('codex', root=self.home / 'manager')
        packages = self.home / '.codex/packages/standalone'
        binary = packages / 'releases/0.166.0/bin/codex'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'vendor executable')
        auth = self.home / '.codex/auth.json'
        auth.write_text('keep authentication')
        self.path = self.home / '.local/bin/codex'
        self.path.symlink_to(binary)
        self.found = install.Installation(self.path, 'installer', '0.166.0', None)
        current = software.plan(manager)
        self.assertEqual(set(current['targets']), {str(self.path), str(packages)})
        result = software.perform(
            manager,
            'uninstall',
            revision=current['revision'],
            confirmed=True,
        )
        self.assertTrue(result['uninstalled'])
        self.assertFalse(self.path.exists())
        self.assertFalse(packages.exists())
        self.assertEqual(auth.read_text(), 'keep authentication')

    def test_bundle_can_only_be_disabled_and_restored(self):
        with patch.dict(os.environ, AIXAND_PROVIDER_BUNDLE_ROOT=str(self.home)):
            current = software.plan(self.manager)
            self.assertTrue(current['bundled'])
            self.assertFalse(current['automatic'])
            with self.assertRaisesRegex(Error, 'included with Aixand'):
                self.perform('uninstall')
            self.perform('disable')
            self.assertTrue(self.path.exists())
            with self.assertRaisesRegex(Error, 'disabled'):
                software.require_enabled(self.manager)
            self.perform('enable')
            software.require_enabled(self.manager)
            self.assertTrue(self.path.exists())

    def test_changed_installation_or_missing_confirmation_never_mutates_files(self):
        revision = software.plan(self.manager)['revision']
        self.path.write_bytes(b'replaced with another CLI')
        with self.assertRaisesRegex(Error, 'installation changed'):
            self.perform('uninstall', revision)
        with self.assertRaises(Error):
            software.validate(self.manager, 'disable', software.plan(self.manager)['revision'], False)
        self.assertTrue(self.path.exists())
        self.assertFalse(software.disabled(self.manager))

    def test_disabling_blocks_every_sdk_launch_entry_point_and_direct_cli_run(self):
        from agent_manager.interfaces.sdk import Client
        self.manager.new_profile('work')
        self.perform('disable')
        client = object.__new__(Client)
        client.manager = self.manager
        async def check():
            calls = [lambda: client.spawn()]
            for call in calls:
                with self.assertRaisesRegex(Error, 'disabled'):
                    await call()
        asyncio.run(check())
        with self.assertRaisesRegex(Error, 'disabled'):
            self.manager._run_native('work', [])

    def test_running_agent_blocks_removal_and_disabling(self):
        with patch('agent_manager.providers.catalog.active_profiles', return_value=['work']):
            for kind in ['uninstall', 'disable']:
                with self.assertRaisesRegex(Error, 'running agents'):
                    self.perform(kind)
        self.assertTrue(self.path.exists())

    def test_unknown_owner_and_redirected_directory_require_manual_removal(self):
        outside = self.home / 'unowned'
        outside.mkdir()
        self.path.unlink()
        self.path.parent.rmdir()
        self.path.parent.symlink_to(outside, target_is_directory=True)
        self.path.write_bytes(b'unowned CLI')
        current = software.plan(self.manager)
        self.assertFalse(current['automatic'])
        self.assertTrue(current['manualCommands'])
        with self.assertRaisesRegex(Error, 'owner could not be verified'):
            self.perform('uninstall')
        self.assertTrue(self.path.exists())

    def test_failed_second_rename_restores_first_target(self):
        versions = self.home / '.local/share/claude'
        versions.mkdir(parents=True)
        original = Path.rename
        def rename(source, target):
            if source == versions:
                raise PermissionError('in use')
            return original(source, target)
        with patch.object(Path, 'rename', rename):
            with self.assertRaisesRegex(Error, 'in use'):
                self.perform('uninstall')
        self.assertTrue(self.path.exists())
        self.assertTrue(versions.exists())

    def test_external_package_manager_removal_is_manual(self):
        prefix = self.home / 'npm'
        package = prefix / 'lib/node_modules/@anthropic-ai/claude-code'
        package.mkdir(parents=True)
        (package / 'package.json').write_text(json.dumps({'name': '@anthropic-ai/claude-code'}))
        binary = package / 'cli.js'
        binary.write_bytes(b'vendor executable')
        self.path = prefix / 'bin/claude'
        self.path.parent.mkdir(parents=True)
        self.path.symlink_to(binary)
        self.found = install.Installation(self.path, 'npm', '2.1.302', None)
        current = software.plan(self.manager)
        self.assertFalse(current['automatic'])
        self.assertEqual(current['command'], [])
        with patch.object(install, 'run_checked') as runner:
            with self.assertRaisesRegex(Error, 'managed externally'):
                self.perform('uninstall')
        runner.assert_not_called()
        self.assertTrue(self.path.exists())
        self.assertEqual(binary.read_bytes(), b'vendor executable')

    def test_copilot_and_antigravity_native_removal_preserves_accounts_and_other_tools(self):
        for provider, executable in [('copilot', 'copilot'), ('antigravity', 'agy')]:
            with self.subTest(provider=provider):
                manager = Manager(provider, root=self.home / 'profiles')
                self.path = self.home / '.local/bin' / executable
                self.path.write_bytes(b'owned executable')
                self.found = install.Installation(self.path, 'installer', '1.0.0', None)
                retained = self.home / ('.copilot/account.json' if provider == 'copilot' else '.gemini/antigravity-cli/account.json')
                retained.parent.mkdir(parents=True, exist_ok=True)
                retained.write_bytes(b'keep accounts')
                plan = software.plan(manager)
                self.assertTrue(plan['automatic'])
                result = software.perform(manager, 'uninstall', revision=plan['revision'], confirmed=True)
                self.assertTrue(result['accountsPreserved'])
                self.assertFalse(self.path.exists())
                self.assertEqual(retained.read_bytes(), b'keep accounts')

    def test_external_copilot_installation_is_not_removed(self):
        manager = Manager('copilot', root=self.home / 'profiles')
        self.path = self.home / 'npm/bin/copilot'
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'external CLI')
        self.found = install.Installation(self.path, 'npm', '1.0.104', None)
        current = software.plan(manager)
        self.assertFalse(current['automatic'])
        with self.assertRaisesRegex(Error, 'managed externally'):
            software.perform(manager, 'uninstall', revision=current['revision'], confirmed=True)
        self.assertEqual(self.path.read_bytes(), b'external CLI')

    def test_verified_npm_uninstall_preserves_native_profile_and_handles_failure(self):
        from types import SimpleNamespace
        manager = Manager('copilot', root=self.home / 'profiles')
        manager.new_profile('work')
        marker = manager.home('work') / 'native-login-marker'; marker.write_text('preserve')
        prefix = self.home / 'npm'
        package = prefix / 'lib/node_modules/@github/copilot'
        package.mkdir(parents=True)
        (package / 'package.json').write_text(json.dumps({'name': '@github/copilot', 'bin': {'copilot': 'cli.js'}}))
        binary = package / 'cli.js'; binary.write_text('fixture')
        self.path = prefix / 'bin/copilot'; self.path.parent.mkdir(parents=True)
        self.path.symlink_to(binary)
        self.found = install.Installation(self.path, 'npm', '1.0.104', None)
        with patch.object(install, 'manager_prefix', return_value=prefix), \
             patch.object(install, 'package_manager_command', side_effect=lambda name, args: [name, *args]):
            current = software.plan(manager)
            self.assertTrue(current['automatic'])
            with patch.object(software.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
                with self.assertRaisesRegex(Error, 'uninstall failed'):
                    software.perform(manager, 'uninstall', revision=current['revision'], confirmed=True)
            self.assertTrue(self.path.exists())
            self.assertFalse(software.disabled(manager))
            def remove(command, **kwargs):
                self.assertEqual(command, ['npm', 'uninstall', '--global', '--prefix', str(prefix), '@github/copilot'])
                self.path.unlink()
                return SimpleNamespace(returncode=0)
            with patch.object(software.subprocess, 'run', side_effect=remove):
                result = software.perform(manager, 'uninstall', revision=current['revision'], confirmed=True)
            self.assertTrue(result['uninstalled'])
            self.assertEqual(marker.read_text(), 'preserve')
            self.assertTrue(software.disabled(manager))


if __name__ == '__main__':
    unittest.main()
