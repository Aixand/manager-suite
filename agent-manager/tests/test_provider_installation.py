"""Local CLI metadata is independent of managed installer support."""
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from agent_manager import Manager
from agent_manager.providers.installation import engine as install
from agent_manager.providers.installation.engine import inspect_external
from agent_manager.providers import catalog as provider_catalog
from agent_manager.runtime import maintenance as provider_operations
from agent_manager.providers.installation import updates as update
from agent_manager.errors import Error


class ProviderInstallationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()

    def binary(self, path, version):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\nprintf "%s\\n" "' + version + '"\n')
        path.chmod(0o755)
        return path

    def test_explicit_npm_prefix_discovery_does_not_run_npm_or_create_cache(self):
        prefix = self.base.resolve() / 'not-created/tools/node'
        for key in ('npm_config_prefix', 'NPM_CONFIG_PREFIX'):
            with self.subTest(key=key), patch.dict(os.environ, {key: str(prefix)}, clear=True), \
                    patch('shutil.which', return_value='/available/npm'), \
                    patch.object(install, 'output') as probe:
                self.assertEqual(install.manager_prefix('npm'), prefix)
                probe.assert_not_called()
                self.assertFalse(prefix.parent.exists())

    def test_unconfigured_npm_prefix_still_uses_the_package_manager(self):
        prefix = self.base.resolve() / 'npm'
        with patch.dict(os.environ, {}, clear=True), \
                patch('shutil.which', return_value='/available/npm'), \
                patch.object(install, 'output', return_value=str(prefix)) as probe:
            self.assertEqual(install.manager_prefix('npm'), prefix)
            probe.assert_called_once_with(['/available/npm', 'prefix', '-g'])

    def test_claude_native_symlink_reports_version_and_installer_without_state(self):
        target = self.binary(self.base / '.local/share/claude/versions/2.1.302', '2.1.302 (Claude Code)')
        path = self.base / '.local/bin/claude'
        path.parent.mkdir(parents=True)
        path.symlink_to(target)
        manager = Manager('claude', root=self.base / 'manager')
        with patch('shutil.which', return_value=str(path)), patch('pathlib.Path.home', return_value=self.base):
            result = inspect_external(manager)
        self.assertEqual((result.version, result.method, result.error), ('2.1.302', 'installer', None))
        self.assertEqual(result.path, path)
        self.assertFalse(manager.state_path.exists())

    def test_antigravity_standalone_reports_real_cli_version(self):
        path = self.binary(self.base / '.local/bin/agy', '1.4.0')
        manager = Manager('antigravity', root=self.base / 'manager')
        with patch('shutil.which', return_value=str(path)), patch('pathlib.Path.home', return_value=self.base):
            result = inspect_external(manager)
        self.assertEqual((result.version, result.method, result.error), ('1.4.0', 'standalone', None))

    def test_package_manager_layouts_and_custom_executables_are_detected_conservatively(self):
        manager = Manager('claude', root=self.base / 'manager')
        for relative, method in [('tools/node_modules/@vendor/cli/bin/cli', 'npm'),
                                 ('tools/Cellar/cli/1.2.3/bin/cli', 'brew'), ('custom/cli', None)]:
            with self.subTest(relative=relative):
                path = self.binary(self.base / relative, 'v1.2.3')
                manager.config['executable'] = str(path)
                result = inspect_external(manager)
                self.assertEqual((result.version, result.method, result.error), ('1.2.3', method, None))

    def test_missing_configured_executable_does_not_fall_back_to_another_cli(self):
        manager = Manager('claude', root=self.base / 'manager')
        manager.config['executable'] = '/missing/custom-claude'
        with patch('shutil.which', return_value=None) as which:
            self.assertIsNone(inspect_external(manager))
        which.assert_called_once_with('/missing/custom-claude')

    def test_default_user_install_is_detected_before_the_shell_path_is_reloaded(self):
        manager = Manager('antigravity', root=self.base / 'manager')
        path = self.binary(self.base / '.local/bin/agy', '1.4.0')
        with patch('shutil.which', return_value=None), patch('pathlib.Path.home', return_value=self.base), \
                patch('platform.system', return_value='Linux'):
            result = inspect_external(manager)
        self.assertEqual((result.path, result.version), (path, '1.4.0'))

    def test_version_probe_failure_retains_the_installed_path(self):
        manager = Manager('antigravity', root=self.base / 'manager')
        path = self.binary(self.base / 'broken-agy', 'no version')
        manager.config['executable'] = str(path)
        result = inspect_external(manager)
        self.assertEqual(result.path, path)
        self.assertIsNone(result.version)
        self.assertEqual(result.error, 'CLI version check failed.')

    def test_discovery_skips_unreadable_path_entries_and_keeps_the_accessible_cli(self):
        command = 'codex.exe' if os.name == 'nt' else 'codex'
        bad = self.binary(self.base / 'untrusted' / command, 'codex 0.162.1')
        good = self.binary(self.base / 'valid' / command, 'codex 0.162.1')
        found = install.Installation(good, None, '0.162.1', None)
        for method in ['resolve', 'is_file']:
            original = getattr(Path, method)

            def inspect_path(path, *args, **kwargs):
                if path == bad:
                    raise OSError(448, 'The path contains an untrusted mount point')
                return original(path, *args, **kwargs)

            with self.subTest(method=method), patch.object(Path, method, inspect_path), \
                    patch.dict(os.environ, PATH=os.pathsep.join([str(bad.parent), str(good.parent)]),
                               LOCALAPPDATA=str(self.base / 'Local')), \
                    patch('pathlib.Path.home', return_value=self.base / 'home'), \
                    patch('agent_manager.providers.installation.engine.manager_prefix', return_value=None), \
                    patch('agent_manager.providers.installation.engine.inspect', return_value=found) as inspect:
                self.assertEqual(install.discover(install.catalog()['codex']), [found])
                inspect.assert_called_once_with(install.catalog()['codex'], good)

    def test_bundled_cli_is_identified_and_excluded_from_independent_maintenance(self):
        for provider in ['codex', 'claude']:
            with self.subTest(provider=provider):
                path = self.binary(self.base / 'runtime/bin' / provider, provider + ' 2.1.302')
                manager = Manager(provider, root=self.base / 'manager')
                with patch.dict(os.environ, AIXAND_PROVIDER_BUNDLE_ROOT=str(self.base / 'runtime')), \
                        patch('agent_manager.providers.installation.engine.provider_executable', return_value=path), \
                        patch('agent_manager.providers.installation.engine.discover', side_effect=lambda spec: [install.inspect(spec, path)]), \
                        patch('shutil.which', return_value=str(path)):
                    self.assertEqual(install.inspect(install.catalog()[provider], path).method, 'bundled')
                    self.assertEqual(inspect_external(manager).method, 'bundled')
                    info = provider_catalog.inspect_installation(manager)
                    self.assertEqual(info['method'], 'bundled')
                    self.assertTrue(info['usable'])
                    self.assertIsNone(info['error'])
                    self.assertFalse(info['canUpdate'])
                    self.assertIn('Update Aixand', info['maintenanceNote'])
                    self.assertTrue(info['software']['bundled'])
                    self.assertFalse(info['software']['automatic'])
                    for kind in ['update', 'repair']:
                        with self.assertRaisesRegex(Error, 'Update Aixand'):
                            provider_operations.validate_operation(manager, dict(kind=kind))
                    from agent_manager.interfaces.cli.parser import Parser
                    args = install.parser(Parser).parse_args([provider, '--reinstall', '--yes'])
                    with patch.object(install, 'perform') as perform:
                        with self.assertRaisesRegex(Error, 'Update Aixand'):
                            install.command(args)
                        perform.assert_not_called()
                    self.assertEqual(install.migration_error([install.inspect(install.catalog()[provider], path)], 'installer'), install.BUNDLED_NOTE)

    def test_windows_native_location_is_detected_without_an_installation_receipt(self):
        with patch('platform.system', return_value='Windows'), patch('pathlib.Path.home', return_value=self.base), \
                patch.dict(os.environ, LOCALAPPDATA=str(self.base / 'Local'), AIXAND_PROVIDER_BUNDLE_ROOT=''):
            for provider in ['codex', 'claude', 'opencode']:
                spec = install.catalog()[provider]
                path = self.binary(install.native_binary(spec), provider + ' 2.1.302')
                self.assertEqual(install.inspect(spec, path).method, 'installer')

    def test_npm_layout_recognizes_windows_path_separators(self):
        from pathlib import PureWindowsPath
        path = PureWindowsPath('C:/tools/node_modules/@openai/codex/bin/codex.exe')
        with patch.dict(os.environ, AIXAND_PROVIDER_BUNDLE_ROOT=''), patch.object(PureWindowsPath, 'resolve', create=True, return_value=path):
            self.assertEqual(install.identify_method(install.catalog()['codex'], path), 'npm')


if __name__ == '__main__':
    unittest.main()
