import contextlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from agent_manager.providers.installation import engine as install, updates as update
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.interfaces.cli.parser import Parser
from agent_manager.errors import Error
from agent_manager.storage.locks import locks
from tests import test_agent_install as fixtures


class UpdateTests(unittest.TestCase):
    receipt = fixtures.InstallTests.receipt
    calls = fixtures.InstallTests.calls
    writes = fixtures.InstallTests.writes
    seed_native = fixtures.InstallTests.seed_native

    def setUp(self):
        fixtures.InstallTests.setUp(self)
        environment = patch.dict(os.environ, XDG_CONFIG_HOME=str(self.home / '.config'),
                                 XDG_DATA_HOME=str(self.home / '.local/share'),
                                 AIXAND_MANAGER_ROOT=str(self.home / '.local/share/aixand/manager'))
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('AGENT_MANAGER_CONFIG', None)
        self.config = self.base / 'providers.toml'
        self.config.write_text('')

    def go(self, *args, picker=None, config=None):
        parsed = update.parser(Parser).parse_args(list(args))
        self.stdout.isatty = sys.stdout.isatty
        with contextlib.redirect_stdout(self.stdout):
            return update.command(parsed, config=config or str(self.config), picker=picker)

    def seed(self, target, method, version=None):
        version = version or ('0.150.0' if target == 'codex' else '2.0.17')
        native = self.seed_native(target, version)
        if method == 'installer':
            return native
        spec = install.catalog()[target]
        if method == 'npm':
            binary = self.base / 'npm/lib/node_modules' / spec['npm_package'] / 'bin' / target
        else:
            directory = 'Caskroom' if spec['brew_cask'] else 'Cellar'
            binary = self.base / 'brew' / directory / spec['brew_package'].rsplit('/', 1)[-1] / version / 'bin' / target
        binary.parent.mkdir(parents=True, exist_ok=True)
        native.rename(binary)
        destination = self.base / method / 'bin' / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(binary)
        return destination

    def test_official_updates_both_providers_without_existing_receipts(self):
        for target, old, new in [('codex', '0.150.0', '0.166.0'), ('opencode', '2.0.17', '2.2.5')]:
            with self.subTest(target=target):
                binary = self.seed(target, 'installer', old)
                self.assertEqual(self.go(target, '--yes'), 0)
                self.assertEqual(install.inspect(install.catalog()[target], binary).version, new)
                receipt = json.loads(self.receipt(target).read_text())
                self.assertEqual((receipt['method'], receipt['previous_version'], receipt['version']),
                                 ('installer', old, new))
                self.assertIn('updated_at', receipt)
        self.assertEqual(len([call for call in self.calls() if call[0] == 'curl']), 2)
        self.assertFalse((self.home / '.agent-home').exists())

    def test_external_package_manager_installations_are_not_updated(self):
        for target, method in [('codex', 'npm'), ('opencode', 'brew')]:
            self.seed(target, method)
            with self.assertRaisesRegex(Error, 'externally'):
                self.go(target)
        self.assertEqual(self.writes(), [])

    def test_brew_is_not_silently_replaced_for_an_exact_update(self):
        self.seed('codex', 'brew')
        with self.assertRaisesRegex(Error, 'externally'):
            self.go('codex', '--yes')
        self.assertEqual(self.writes(), [])

    def test_picker_includes_only_installed_configured_providers(self):
        self.seed('opencode', 'installer')
        self.config.write_text('[providers.claude]\nexecutable="claude"\n')
        def picker(title, headers, rows, detail, **kwargs):
            self.assertEqual(title, 'Update an agent')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][:3], ('OpenCode 2', '2.0.17', 'Official native installer'))
            self.assertEqual(kwargs['disabled'], set())
            return 0
        with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stdout, 'isatty', return_value=True):
            self.assertEqual(self.go(picker=picker), 0)
        self.assertEqual([call[0] for call in self.writes()], ['curl', 'driver'])
        self.assertIn(['driver', 'opencode', '--version', '2.2.5', '--no-modify-path'], self.writes())

    def test_configured_missing_override_is_not_replaced_by_default(self):
        self.seed('codex', 'installer')
        self.config.write_text('[providers.codex]\nexecutable="/missing/custom-codex"\n')
        with self.assertRaisesRegex(Error, 'Configured executable is not installed'):
            self.go('codex')
        self.assertEqual(self.writes(), [])
        self.assertFalse(self.state.exists())

    def test_configured_installer_entry_point_is_respected(self):
        binary = self.seed('codex', 'installer')
        self.config.write_text('[providers.codex]\nexecutable=' + json.dumps(str(binary)) + '\n')
        self.assertEqual(self.go('codex'), 0)
        self.assertEqual(install.inspect(install.catalog()['codex'], binary).version, '0.166.0')

    def test_unknown_custom_binary_is_disabled_in_picker(self):
        binary = self.seed('codex', 'installer')
        custom = self.base / 'custom-codex'
        binary.rename(custom)
        self.config.write_text('[providers.codex]\nexecutable=' + json.dumps(str(custom)) + '\n')
        self.seed('opencode', 'installer')
        def picker(title, headers, rows, detail, **kwargs):
            self.assertIn('Unknown installation method', rows[0][-1])
            self.assertEqual(kwargs['disabled'], {0})
            self.assertEqual(kwargs['initial'], 1)
            return None
        with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stdout, 'isatty', return_value=True):
            self.assertEqual(self.go(picker=picker), 0)
        self.assertEqual(self.writes(), [])
        self.assertFalse(self.state.exists())

    def test_configured_path_inside_package_is_not_a_stable_entry_point(self):
        binary = self.seed('codex', 'npm')
        self.config.write_text('[providers.codex]\nexecutable=' + json.dumps(str(binary.resolve())) + '\n')
        with self.assertRaisesRegex(Error, 'externally|not the managed entry point'):
            self.go('codex')
        self.assertEqual(self.writes(), [])

    def test_other_brew_package_is_not_mistaken_for_supported_formula(self):
        binary = self.seed('opencode', 'brew')
        previous = binary.resolve()
        other = Path(str(previous).replace('/opencode-v2/', '/opencode/'))
        other.parent.mkdir(parents=True)
        previous.rename(other)
        binary.unlink()
        binary.symlink_to(other)
        with self.assertRaisesRegex(Error, 'externally|Unknown installation method'):
            self.go('opencode')
        self.assertEqual(self.writes(), [])

    def test_other_npm_prefix_is_not_updated_through_current_npm(self):
        original = self.seed('codex', 'npm')
        other_prefix = self.base / 'other-npm'
        (self.base / 'npm').rename(other_prefix)
        binary = other_prefix / 'bin/codex'
        binary.unlink()
        binary.symlink_to(other_prefix / 'lib/node_modules/@openai/codex/bin/codex')
        self.config.write_text('[providers.codex]\nexecutable=' + json.dumps(str(binary)) + '\n')
        with self.assertRaisesRegex(Error, 'externally|not the managed entry point'):
            self.go('codex')
        self.assertFalse(original.exists())
        self.assertEqual(self.writes(), [])

    def test_dry_run_and_listing_are_read_only(self):
        self.seed('codex', 'installer')
        self.seed('opencode', 'installer')
        for args in [('codex', '--dry-run'), ('opencode', '--dry-run'), ('--list',)]:
            self.assertEqual(self.go(*args), 0)
        self.assertIn('--release 0.166.0', self.stdout.getvalue())
        self.assertEqual(self.writes(), [])
        self.assertFalse(self.state.exists())

    def test_missing_provider_does_not_become_a_new_install(self):
        with self.assertRaisesRegex(Error, 'not installed'):
            self.go('codex')
        with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stdout, 'isatty', return_value=True):
            with self.assertRaisesRegex(Error, 'No supported configured providers'):
                self.go()
        self.assertEqual(self.writes(), [])
        self.assertFalse(self.state.exists())

    def test_noninteractive_root_requires_target_and_never_picks_all(self):
        self.seed('codex', 'installer')
        for args in [(), ('--yes',), ('--dry-run',)]:
            with self.subTest(args=args), patch.object(update.tui, 'pick') as picker:
                with self.assertRaisesRegex(Error, 'Specify a provider'):
                    self.go(*args)
                picker.assert_not_called()
        self.assertEqual(self.writes(), [])

    def test_multiple_methods_are_rejected_before_any_update(self):
        self.seed('codex', 'npm')
        self.seed('codex', 'installer')
        with self.assertRaisesRegex(Error, 'migrate it manually'):
            self.go('codex')
        self.assertEqual(self.writes(), [])

    def test_missing_dependency_does_not_fall_back_to_another_method(self):
        self.seed('codex', 'installer')
        which = install.shutil.which
        with patch.object(install.shutil, 'which', side_effect=lambda name, *a, **kw: None if name == 'curl' else which(name, *a, **kw)):
            with self.assertRaisesRegex(Error, 'Missing: curl'):
                self.go('codex')
        self.assertEqual(self.writes(), [])

    def test_broken_or_incompatible_installation_requires_explicit_repair(self):
        binary = self.seed('opencode', 'installer', '1.18.34')
        with self.assertRaisesRegex(Error, 'requires OpenCode 2'):
            self.go('opencode')
        binary.write_text('#!' + sys.executable + '\nraise SystemExit(1)\n')
        with self.assertRaisesRegex(Error, 'Version check failed'):
            self.go('opencode')
        self.assertEqual(self.writes(), [])

    def test_install_lock_also_blocks_updates(self):
        self.seed('codex', 'installer')
        with locks(install.state_root() / 'locks', 'install-codex'):
            with self.assertRaisesRegex(Error, 'Busy: install-codex'):
                self.go('codex')
        self.assertEqual(self.writes(), [])

    def test_installation_disappearing_before_lock_is_not_reinstalled(self):
        binary = self.seed('codex', 'installer')
        perform = install.perform
        def remove_then_perform(*args, **kwargs):
            binary.unlink()
            return perform(*args, **kwargs)
        with patch.object(install, 'perform', side_effect=remove_then_perform):
            with self.assertRaisesRegex(Error, 'not installed'):
                self.go('codex')
        self.assertEqual(self.writes(), [])
        self.assertFalse(self.receipt('codex').exists())

    def test_configuration_is_rechecked_after_selection(self):
        self.seed('codex', 'installer')
        def picker(*args, **kwargs):
            self.config.write_text('[providers.codex]\nexecutable="/missing/codex"\n')
            return 0
        with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stdout, 'isatty', return_value=True):
            with self.assertRaisesRegex(Error, 'not installed'):
                self.go(picker=picker)
        self.assertEqual(self.writes(), [])

    def test_failed_update_preserves_last_successful_receipt(self):
        self.seed('codex', 'installer')
        self.receipt('codex').parent.mkdir(parents=True)
        previous = '{"version":"0.150.0"}\n'
        self.receipt('codex').write_text(previous)
        with patch.dict(os.environ, TEST_FAIL='install'):
            with self.assertRaisesRegex(Error, 'exit 17'):
                self.go('codex')
        self.assertEqual(self.receipt('codex').read_text(), previous)

    def test_failed_verification_never_records_update_success(self):
        self.seed('opencode', 'installer')
        with patch.dict(os.environ, TEST_RESULT_VERSION='1.18.34'):
            with self.assertRaisesRegex(Error, 'failed verification'):
                self.go('opencode')
        self.assertFalse(self.receipt('opencode').exists())
        self.assertNotIn('Update completed', self.stdout.getvalue())

    def test_unchanged_version_is_reported_without_claiming_latest(self):
        self.seed('codex', 'installer', '0.166.0')
        self.assertEqual(self.go('codex'), 0)
        self.assertIn('Keeping compatible', self.stdout.getvalue())
        self.assertEqual(self.writes(), [])

    def test_environment_and_default_configuration_paths_are_honored(self):
        self.seed('codex', 'installer')
        self.config.write_text('[providers.codex]\nexecutable="/missing/codex"\n')
        parsed = update.parser(Parser).parse_args(['codex', '--dry-run'])
        with patch.dict(os.environ, AGENT_MANAGER_CONFIG=str(self.config)):
            with self.assertRaisesRegex(Error, 'not installed'):
                update.command(parsed)
        default = self.home / '.local/share/aixand/manager/agent/providers.toml'
        default.parent.mkdir(parents=True)
        default.write_text(self.config.read_text())
        with self.assertRaisesRegex(Error, 'not installed'):
            update.command(parsed)
        with self.assertRaisesRegex(Error, 'configuration does not exist'):
            self.go('codex', config=str(self.base / 'missing-config'))
        self.assertEqual(self.writes(), [])


class UpdateRoutingTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(update, 'candidate', side_effect=AssertionError('Routing tests must not inspect or update real provider installations'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_both_command_forms_honor_global_configuration_and_home(self):
        for command_args in [['update', 'codex'], ['codex', 'update'], ['opencode', 'update']]:
            argv = ['--config', '/tmp/providers.toml', '--home', '/tmp/profiles', *command_args, '--dry-run']
            with self.subTest(argv=argv), patch.object(update, 'command', return_value=0) as command, \
                    patch('agent_manager.interfaces.cli.dispatch.Manager'):
                self.assertEqual(execute(argv), 0)
                args = command.call_args.args[0]
                self.assertIn(args.target, ('codex', 'opencode'))
                self.assertTrue(args.dry_run)
                self.assertEqual(command.call_args.kwargs, {'config': '/tmp/providers.toml', 'home': '/tmp/profiles', 'progress': None})

    def test_root_update_can_open_picker(self):
        with patch.object(update, 'command', return_value=0) as command:
            self.assertEqual(execute(['update']), 0)
            self.assertIsNone(command.call_args.args[0].target)

    def test_update_option_values_profile_names_and_provider_args_are_preserved(self):
        with patch('agent_manager.interfaces.cli.dispatch.Manager') as manager:
            manager.return_value._run_native.return_value = 0
            self.assertEqual(execute(['--home', 'update', 'codex', 'run', 'update', '--', 'update']), 0)
            manager.assert_called_once_with('codex', None, 'update')
            manager.return_value.resolve_profile.assert_called_once_with('update')
            self.assertEqual(manager.return_value._run_native.call_args.args, (manager.return_value.resolve_profile.return_value, ['update']))

    def test_forwarded_arguments_and_unsupported_options_fail(self):
        for args in [['codex', 'update', '--npm'], ['update', 'unknown-provider']]:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                with patch('agent_manager.interfaces.cli.dispatch.Manager'):
                    execute(args)
            self.assertEqual(error.exception.code, 2)
        for args in [['update', 'codex', '--', '--force'], ['codex', 'update', '--', '--force']]:
            with self.subTest(args=args), self.assertRaisesRegex(Error, 'do not accept provider arguments'), \
                    patch('agent_manager.interfaces.cli.dispatch.Manager') as manager:
                execute(args)
            manager.assert_not_called()
        with patch('agent_manager.interfaces.cli.dispatch.Manager'), self.assertRaisesRegex(Error, 'no supported update recipe'):
            execute(['unknown-provider', 'update'])

    def test_help_does_not_probe_provider_or_installations(self):
        for args in [['--help'], ['update', '--help'], ['codex', 'update', '--help'], ['opencode', 'update', '--help']]:
            with self.subTest(args=args), patch.object(update, 'candidate') as candidate, \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
                execute(args)
            self.assertEqual(error.exception.code, 0)
            candidate.assert_not_called()
