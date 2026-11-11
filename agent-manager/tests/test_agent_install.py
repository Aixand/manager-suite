import contextlib
import curses
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_manager.providers.installation import engine as install
from agent_manager.interfaces.cli import tui
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.interfaces.cli.parser import Parser
from agent_manager.errors import Error
from agent_manager.storage.locks import locks


# Real child processes emulate package managers and downloadable installers.
# They write only under TEST_INSTALL_ROOT, never in the user's agent homes.
TOOL = r'''
import json, os, pathlib, shlex, sys, time
root = pathlib.Path(os.environ['TEST_INSTALL_ROOT'])
packages = {'codex': '@openai/codex', 'opencode': '@opencode/cli', 'copilot': '@github/copilot'}
versions = {'codex': '0.160.0', 'opencode': '2.0.23', 'copilot': '1.0.102', 'antigravity': '1.4.0'}
executables = {'antigravity': 'agy'}
tool, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
with (root / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps([tool, *args]) + '\n')
if tool == 'node':
    print(os.environ.get('TEST_NODE_VERSION', 'v22.20.0'))
    sys.exit(0)
if tool in ('npm', 'brew') and args in (['prefix', '-g'], ['--prefix']):
    print(root / tool)
    sys.exit(0)
if tool == 'curl':
    if os.environ.get('TEST_FAIL') == 'download':
        sys.exit(22)
    target = 'opencode' if '/v2/' in args[-1] else 'copilot' if 'copilot-install' in args[-1] else 'antigravity' if 'antigravity.google' in args[-1] else 'codex'
    script = pathlib.Path(args[args.index('--output') + 1])
    content = ('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' +
               shlex.quote(str(root / 'tools/driver')) + ' ' + target + ' "$@"\n').encode()
    if '--dump-header' in args:
        pathlib.Path(args[args.index('--dump-header') + 1]).write_text(
            'HTTP/1.1 302 Found\r\nContent-Length: 999\r\n\r\nHTTP/2 200\r\nContent-Length: ' + str(len(content)) + '\r\n\r\n')
    with script.open('wb') as stream:
        for offset in range(0, len(content), 16):
            stream.write(content[offset:offset + 16]); stream.flush()
            if os.environ.get('TEST_SLOW_DOWNLOAD'): time.sleep(.08)
    sys.exit(0)
if os.environ.get('TEST_FAIL') == 'install':
    sys.exit(17)
if os.environ.get('TEST_FAIL') == 'missing':
    sys.exit(0)
target = args[0] if tool == 'driver' else next(key for key, package in packages.items() if args[-1].startswith(package + '@')) if tool == 'npm' else 'copilot' if 'copilot-cli' in args[-1] else 'codex' if 'codex' in args[-1] else 'opencode'
version = versions[target]
if tool == 'npm' and args[-1].rsplit('@', 1)[-1] != 'latest':
    version = args[-1].rsplit('@', 1)[-1]
if tool == 'driver':
    flag = '--release' if target == 'codex' else '--version'
    if flag in args:
        version = args[args.index(flag) + 1]
    if target == 'copilot' and os.environ.get('VERSION'):
        version = os.environ['VERSION']
version = os.environ.get('TEST_RESULT_VERSION', version)
if tool == 'driver':
    binary = root / ('home/.opencode/bin/opencode' if target == 'opencode' else 'home/.local/bin/' + executables.get(target, target))
    destination = binary
elif tool == 'npm':
    package = packages[target]
    binary = root / 'npm/lib/node_modules' / package / 'bin' / target
    destination = root / 'npm/bin' / target
else:
    package = 'copilot-cli' if target == 'copilot' else 'codex' if target == 'codex' else 'opencode-v2'
    binary = root / 'brew' / ('Caskroom' if target in ('codex', 'copilot') else 'Cellar') / package / version / 'bin' / target
    destination = root / 'brew/bin' / target
binary.parent.mkdir(parents=True, exist_ok=True)
binary.write_text('#!' + sys.executable + '\nprint(' + repr(target + ' ' + version) + ')\n')
binary.chmod(0o700)
if binary != destination:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    destination.symlink_to(binary)
'''


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.tools = self.base / 'tools'
        self.tools.mkdir()
        for name in ('npm', 'node', 'brew', 'curl', 'driver'):
            path = self.tools / name
            path.write_text('#!' + sys.executable + '\n' + TOOL)
            path.chmod(0o700)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.state = self.base / 'state'
        self.env = patch.dict(os.environ, {
            'PATH': os.pathsep.join(map(str, [self.base / 'npm/bin', self.base / 'brew/bin', self.tools, '/usr/bin', '/bin'])),
            'XDG_STATE_HOME': str(self.state), 'TEST_INSTALL_ROOT': str(self.base),
            'AGENT_MANAGER_HOME': str(self.base / 'profiles'),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        for stub in (patch.object(Path, 'home', return_value=self.home),
                     patch.object(install.platform, 'system', return_value='Linux'),
                     patch.object(install.platform, 'machine', return_value='x86_64'),
                     patch.object(sys.stdin, 'isatty', return_value=False),
                     patch.object(sys.stdout, 'isatty', return_value=False)):
            stub.start()
            self.addCleanup(stub.stop)
        self.stdout = io.StringIO()

    def go(self, *args, picker=None, progress=None):
        parsed = install.parser(Parser).parse_args(list(args))
        self.stdout.isatty = sys.stdout.isatty
        with contextlib.redirect_stdout(self.stdout):
            return install.command(parsed, picker=picker, progress=progress)

    def test_install_progress_measures_download_bytes_and_verifies_before_completion(self):
        events = []
        with patch.dict(os.environ, TEST_SLOW_DOWNLOAD='1'):
            self.go('codex', '--installer', '--yes', progress=lambda event: events.append(event['progress']))
        downloads = [event['transfer'] for event in events if 'transfer' in event]
        self.assertGreater(len(downloads), 2)
        self.assertTrue(any(0 < row['completed'] < row['total'] for row in downloads if row['total']))
        self.assertTrue(all(row['total'] != 999 for row in downloads))
        self.assertEqual(downloads[-1]['completed'], downloads[-1]['total'])
        stages = [event['stage'] for event in events]
        self.assertLess(stages.index('downloading'), stages.index('installing'))
        self.assertEqual(stages[-2:], ['verifying', 'complete'])
        self.assertEqual(events[-1]['steps'], dict(completed=4, total=4))
        self.assertTrue(self.receipt('codex').exists())

    def test_custom_manager_home_owns_installation_receipt(self):
        root = self.base / 'isolated-manager-state'
        args = install.parser(Parser).parse_args(['codex', '--installer', '--yes'])
        with contextlib.redirect_stdout(self.stdout):
            self.assertEqual(install.command(args, home=root), 0)
        self.assertTrue((root / 'installations/codex.json').exists())
        self.assertFalse(self.receipt('codex').exists())

    def test_cli_callback_reports_native_steps_without_inventing_transfer_percentages(self):
        events = []
        with contextlib.redirect_stdout(self.stdout):
            execute(['install', 'codex', '--installer', '--yes'], progress=events.append)
        values = [event['progress'] for event in events]
        self.assertEqual(values[-1]['steps'], dict(completed=4, total=4))
        self.assertEqual([row['stage'] for row in values][-3:], ['installing', 'verifying', 'complete'])
        self.assertTrue(any('transfer' in row for row in values))

    def test_failed_install_or_verification_never_reports_complete(self):
        for environment, stage in [({'TEST_FAIL': 'install'}, 'installing'),
                                   ({'TEST_RESULT_VERSION': 'invalid'}, 'verifying'),
                                   ({'TEST_FAIL': 'download'}, 'downloading')]:
            events = []
            with self.subTest(stage=stage), patch.dict(os.environ, environment), self.assertRaises(Error):
                self.go('codex', '--installer', '--reinstall', '--yes', progress=events.append)
            self.assertEqual(events[-1]['progress']['stage'], stage)
            self.assertNotIn('complete', [event['progress']['stage'] for event in events])
            self.assertFalse(self.receipt('codex').exists())

    def test_dry_run_emits_no_work_progress_and_kept_install_reports_verified(self):
        events = []
        self.go('codex', '--installer', '--dry-run', progress=events.append)
        self.assertEqual(events, [])
        self.seed_native()
        self.go('codex', '--yes', progress=events.append)
        self.assertEqual(events[-1]['progress']['steps'], dict(completed=1, total=1))
        self.assertEqual(events[-1]['progress']['stage'], 'complete')
        self.assertEqual(self.writes(), [])

    def test_unknown_or_redirect_transfer_sizes_do_not_claim_a_percentage(self):
        from agent_manager.providers.installation.progress import transfer_size
        headers = self.base / 'headers'
        self.assertIsNone(transfer_size(headers))
        for content in ('HTTP/2 302\nContent-Length: 123\n\n', 'HTTP/2 200\n\n',
                        'HTTP/2 200\nContent-Length: 123\nTransfer-Encoding: chunked\n\n',
                        'HTTP/2 200\nContent-Length: broken\n\n', 'HTTP/2 200\nContent-Length: 123\n'):
            headers.write_text(content)
            self.assertIsNone(transfer_size(headers))
        headers.write_text('HTTP/1.1 302 Found\r\nContent-Length: 999\r\n\r\nHTTP/2 200\r\ncontent-length: 123\r\n\r\n')
        self.assertEqual(transfer_size(headers), 123)

    def receipt(self, target):
        return Path(os.environ['AGENT_MANAGER_HOME']) / 'installations' / (target + '.json')

    def calls(self):
        path = self.base / 'calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def writes(self):
        return [call for call in self.calls() if call[0] in ('curl', 'driver') or
                (len(call) > 1 and call[1] in ('install', 'reinstall', 'upgrade'))]

    def seed_native(self, target='codex', version='0.162.1'):
        binary = install.native_binary(install.catalog()[target])
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text('#!' + sys.executable + '\nprint(' + repr(target + ' ' + version) + ')\n')
        binary.chmod(0o700)
        return binary

    def test_package_manager_options_are_rejected_without_installation(self):
        for flag in ('--npm', '--brew', '--method=npm', '--method=brew'):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.go('codex', flag, '--yes')
        self.assertEqual(self.writes(), [])

    def test_official_installers_use_correct_urls_and_clean_temporary_scripts(self):
        for target, suffix in [('codex', '/codex/install.sh'), ('opencode', '/v2/install')]:
            with self.subTest(target=target):
                self.assertEqual(self.go(target, '--installer', '--yes'), 0)
                calls = [call for call in self.calls() if call[0] == 'curl']
                self.assertTrue(calls[-1][-1].endswith(suffix))
                downloaded = Path(calls[-1][calls[-1].index('--output') + 1])
                self.assertFalse(downloaded.exists())
                self.assertTrue(install.native_binary(install.catalog()[target]).exists())
                self.assertEqual(json.loads(self.receipt(target).read_text())['method'], 'installer')
        self.assertIn('Add ', self.stdout.getvalue())


    def test_official_copilot_and_pinned_antigravity_recipes(self):
        self.assertEqual(self.go('copilot', '--installer', '--yes'), 0)
        self.assertEqual(json.loads(self.receipt('copilot').read_text())['method'], 'installer')
        self.assertEqual(self.go('antigravity', '--installer', '--dry-run'), 0)
        with self.assertRaisesRegex(Error, 'not approved'):
            self.go('antigravity', '--installer', '--version', '1.4.7', '--reinstall', '--yes')

    def test_native_failed_reinstall_restores_previous_cli_and_receipt(self):
        self.go('codex', '--installer', '--yes')
        binary = install.native_binary(install.catalog()['codex'])
        previous, receipt = binary.read_bytes(), self.receipt('codex').read_bytes()
        for environment in ({'TEST_FAIL': 'install'}, {'TEST_RESULT_VERSION': 'invalid'}, {'TEST_RESULT_VERSION': '0.163.0'}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment), self.assertRaises(Error):
                self.go('codex', '--installer', '--reinstall', '--yes')
            self.assertEqual(binary.read_bytes(), previous)
            self.assertEqual(self.receipt('codex').read_bytes(), receipt)
            self.assertEqual(list(binary.parent.glob('*.agent-backup-*')), [])

    def test_copilot_version_is_passed_as_environment_to_official_script(self):
        self.go('copilot', '--installer', '--version', '1.0.103', '--yes')
        self.assertEqual(json.loads(self.receipt('copilot').read_text())['version'], '1.0.103')

    def test_setup_recipes_only_expose_native_methods_for_each_host(self):
        for system in ('Linux', 'Darwin', 'Windows'):
            with self.subTest(system=system), patch.object(install.platform, 'system', return_value=system):
                copilot = install.setup_guide('copilot')
                self.assertEqual([row['id'] for row in copilot['methods']], [] if system == 'Windows' else ['installer'])
                agy = install.setup_guide('antigravity')
                self.assertEqual([row['id'] for row in agy['methods']], ['installer'])
                self.assertIn('--version 1.4.12', agy['methods'][0]['command'])

    def test_low_level_execution_rejects_package_managers_before_mutation(self):
        spec = install.catalog()['codex']
        for method in ('npm', 'brew'):
            with self.subTest(method=method), self.assertRaisesRegex(Error, 'native installers'):
                install.perform('codex', spec, method, '0.164.0', True, True)
        self.assertEqual(self.writes(), [])

    def test_explicit_versions_are_forwarded_and_checked(self):
        self.assertEqual(self.go('codex', '--installer', '--version', 'v0.162.1'), 0)
        self.assertIn(['driver', 'codex', '--release', '0.162.1'], self.calls())
        self.assertEqual(self.go('opencode', '--installer', '--version', '2.0.18'), 0)
        self.assertIn(['driver', 'opencode', '--version', '2.0.18', '--no-modify-path'], self.calls())
        with self.assertRaisesRegex(Error, 'different version'):
            self.go('codex', '--installer', '--version', '0.164.0')

    def test_rejects_invalid_versions_and_old_opencode_before_installing(self):
        for version in ('--force', '1.0', 'https://example.com/pkg', '2.0.0;echo bad'):
            with self.subTest(version=version), self.assertRaisesRegex(Error, 'exact release'):
                self.go('codex', '--installer', '--version=' + version)
        with self.assertRaisesRegex(Error, 'supported range'):
            self.go('opencode', '--installer', '--version', '1.18.34')
        self.assertEqual(self.writes(), [])

    def test_dry_run_and_listing_write_no_state_or_installations(self):
        for args in [('codex', '--installer', '--dry-run'), ('opencode', '--installer', '--dry-run'),
                     ('--list',)]:
            self.assertEqual(self.go(*args), 0)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.writes(), [])
        self.assertNotIn('brew:', self.stdout.getvalue())
        self.assertNotIn('npm:', self.stdout.getvalue())


    def test_existing_install_is_kept_without_receipt_or_download(self):
        binary = self.seed_native()
        original = binary.read_bytes()
        for args in [('codex', '--yes'), ('codex', '--installer'), ('codex', '--dry-run')]:
            self.assertEqual(self.go(*args), 0)
        self.assertEqual(binary.read_bytes(), original)
        self.assertEqual(self.writes(), [])
        self.assertFalse(self.state.exists())
        self.assertIn('Keeping compatible', self.stdout.getvalue())

    def test_external_npm_installation_is_preserved_and_requires_manual_migration(self):
        native = self.seed_native()
        package = self.base / 'npm/lib/node_modules/@openai/codex/bin/codex'
        package.parent.mkdir(parents=True)
        native.rename(package)
        command = self.base / 'npm/bin/codex'
        command.parent.mkdir(parents=True)
        command.symlink_to(package)
        original = package.read_bytes()
        with self.assertRaisesRegex(Error, 'externally|another method'):
            self.go('codex', '--installer', '--yes', '--reinstall')
        self.assertEqual(package.read_bytes(), original)
        self.assertEqual(self.writes(), [])

    def test_old_opencode_requires_explicit_reinstall(self):
        binary = self.seed_native('opencode', '1.18.34')
        with self.assertRaisesRegex(Error, 'requires OpenCode 2'):
            self.go('opencode', '--yes')
        self.assertEqual(self.go('opencode', '--installer', '--reinstall', '--yes'), 0)
        self.assertEqual(install.inspect(install.catalog()['opencode'], binary).version, '2.2.5')

    def test_missing_dependencies_and_unsupported_platform_do_not_fallback(self):
        (self.tools / 'curl').unlink()
        which = install.shutil.which
        with patch.object(install.shutil, 'which', side_effect=lambda name, *a, **kw: None if name == 'curl' else which(name, *a, **kw)):
            with self.assertRaisesRegex(Error, 'Missing: curl'):
                self.go('codex', '--installer')
        with patch.object(install.platform, 'machine', return_value='riscv64'):
            with self.assertRaisesRegex(Error, 'x64 or ARM64'):
                self.go('codex', '--installer')
        with patch.object(install.platform, 'system', return_value='Windows'):
            with self.assertRaisesRegex(Error, 'does not support|installable|No versioned installation method'):
                self.go('copilot', '--installer')
        self.assertEqual(self.writes(), [])

    def test_native_installation_needs_no_node_or_package_manager(self):
        which = install.shutil.which
        with patch.object(install.shutil, 'which', side_effect=lambda name, *a, **kw: None if name in ('node','npm','brew') else which(name, *a, **kw)):
            self.assertEqual(self.go('codex', '--installer', '--yes'), 0)
        self.assertFalse(any(call[0] in ('node','npm','brew') for call in self.calls()))

    def test_failures_never_record_success_or_run_failed_download(self):
        for stage, message in [('download', 'exit 22'), ('install', 'exit 17'), ('missing', 'no executable')]:
            with self.subTest(stage=stage), patch.dict(os.environ, TEST_FAIL=stage):
                before = len([call for call in self.calls() if call[0] == 'driver'])
                with self.assertRaisesRegex(Error, message):
                    self.go('opencode', '--installer', '--yes')
                self.assertFalse(self.receipt('opencode').exists())
                if stage == 'download':
                    self.assertEqual(before, len([call for call in self.calls() if call[0] == 'driver']))

    def test_incompatible_installed_version_fails_verification(self):
        with patch.dict(os.environ, TEST_RESULT_VERSION='1.18.34'):
            with self.assertRaisesRegex(Error, 'failed verification'):
                self.go('opencode', '--installer')
        self.assertFalse(self.receipt('opencode').exists())

    def test_requested_version_mismatch_fails_verification(self):
        with patch.dict(os.environ, TEST_RESULT_VERSION='0.160.0'):
            with self.assertRaisesRegex(Error, 'Requested 0.162.1'):
                self.go('codex', '--installer', '--version', '0.162.1')
        self.assertFalse(self.receipt('codex').exists())

    def test_target_lock_prevents_competing_install(self):
        with locks(install.state_root() / 'locks', 'install-codex'):
            with self.assertRaisesRegex(Error, 'Busy: install-codex'):
                self.go('codex', '--installer')
        self.assertEqual(self.writes(), [])

    def test_occupied_destination_is_preserved(self):
        binary = install.native_binary(install.catalog()['codex'])
        binary.parent.mkdir(parents=True)
        binary.write_text('unrelated file')
        with self.assertRaisesRegex(Error, 'destination is occupied'):
            self.go('codex', '--installer')
        self.assertEqual(binary.read_text(), 'unrelated file')
        self.assertEqual(self.writes(), [])

    def test_multiple_installations_are_detected(self):
        self.seed_native()
        alternate = self.base / 'npm/lib/node_modules/@openai/codex/bin/codex'
        alternate.parent.mkdir(parents=True)
        alternate.write_text('#!' + sys.executable + '\nprint("codex 0.162.1")\n')
        alternate.chmod(0o700)
        command = self.base / 'npm/bin/codex'
        command.parent.mkdir(parents=True)
        command.symlink_to(alternate)
        with self.assertRaisesRegex(Error, 'migrate it manually|another method'):
            self.go('codex', '--installer', '--reinstall')
        self.assertEqual(self.writes(), [])

    def test_noninteractive_missing_choices_fail_without_a_picker(self):
        for args in [(), ('--yes',), ('--installer',), ('codex',)]:
            with self.subTest(args=args), patch.object(tui, 'pick') as picker:
                with self.assertRaisesRegex(Error, 'Specify a provider|non-interactive native'):
                    self.go(*args)
                picker.assert_not_called()
        self.assertEqual(self.writes(), [])

    def test_yes_selects_official_installer_for_a_new_install(self):
        self.assertEqual(self.go('opencode', '--yes'), 0)
        self.assertEqual(json.loads(self.receipt('opencode').read_text())['method'], 'installer')

    def test_interactive_target_then_native_action(self):
        seen = []
        def picker(title, headers, rows, detail, **kwargs):
            seen.append((title, rows))
            return 1 if len(seen) == 1 else 0
        with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stdout, 'isatty', return_value=True):
            self.assertEqual(self.go(picker=picker), 0)
        self.assertEqual([title for title, _ in seen], ['Install an agent', 'Install OpenCode 2'])
        self.assertEqual(json.loads(self.receipt('opencode').read_text())['method'], 'installer')

    def test_cancel_at_either_step_has_no_side_effects(self):
        for selections in [[None], [0, None]]:
            choices = iter(selections)
            with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stdout, 'isatty', return_value=True):
                self.assertEqual(self.go(picker=lambda *a, **kw: next(choices)), 0)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.writes(), [])

    def test_explicit_target_skips_target_picker_and_keep_is_default(self):
        self.seed_native()
        def picker(title, headers, rows, detail, **kwargs):
            self.assertEqual(title, 'Install Codex')
            self.assertEqual(rows[0][0], 'Keep installed version')
            return 0
        with patch.object(sys.stdin, 'isatty', return_value=True), patch.object(sys.stdout, 'isatty', return_value=True):
            self.assertEqual(self.go('codex', picker=picker), 0)
        self.assertEqual(self.writes(), [])


class InstallRoutingTests(unittest.TestCase):
    def test_root_install_routes_before_manager_and_opencode_shorthand(self):
        for argv in [['install', 'opencode'], ['--home', 'install', '--config', '/missing', 'install', 'codex', '--installer'],
                     ['--home=/tmp/install', 'install', 'opencode', '--method', 'installer']]:
            with self.subTest(argv=argv), patch('agent_manager.providers.installation.engine.command', return_value=0) as command, \
                    patch('agent_manager.interfaces.cli.dispatch.Manager') as manager:
                self.assertEqual(execute(argv), 0)
                manager.assert_not_called()
                self.assertIn(command.call_args.args[0].target, ('codex', 'opencode'))

    def test_install_word_in_provider_arguments_is_forwarded(self):
        with patch('agent_manager.interfaces.cli.dispatch.Manager') as manager:
            manager.return_value._run_native.return_value = 0
            self.assertEqual(execute(['codex', 'run', 'main', '--', 'install', '--npm']), 0)
            manager.return_value.resolve_profile.assert_called_once_with('main')
            self.assertEqual(manager.return_value._run_native.call_args.args, (manager.return_value.resolve_profile.return_value, ['install', '--npm']))

    def test_opencode_profile_name_is_not_treated_as_a_shorthand(self):
        with patch('agent_manager.interfaces.cli.dispatch.Manager') as manager:
            manager.return_value.new_profile.return_value = 0
            self.assertEqual(execute(['codex', 'profile', 'new', 'opencode', '--json']), 0)
            manager.return_value.new_profile.assert_called_once_with('opencode', model=None)

    def test_method_options_conflict_and_other_targets_are_rejected(self):
        for args in [['install', 'unknown-provider'], ['install', 'codex', '--npm', '--brew'],
                     ['install', 'codex', '--npm', '--method', 'npm']]:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                execute(args)
            self.assertEqual(error.exception.code, 2)
        with self.assertRaisesRegex(Error, 'do not accept provider arguments'):
            execute(['install', 'codex', '--', '--force'])

    def test_help_works_without_provider_executables(self):
        for args in [['--help'], ['install', '--help']]:
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
                execute(args)
            self.assertEqual(error.exception.code, 0)


class InstallTuiTests(unittest.TestCase):
    def test_unavailable_rows_cannot_be_selected(self):
        from tests.test_continue import Screen
        with patch.object(curses, 'curs_set'):
            screen = Screen([10, curses.KEY_DOWN, 10])
            result = tui._screen(screen, 'Install', ['Method', 'Status', 'Details'],
                                 [('installer', 'Unavailable', 'Missing curl'), ('installer', 'Ready', 'Official')],
                                 '', disabled={0})
            self.assertEqual(result, 1)
            self.assertTrue(any('Missing curl' in line for line in screen.lines))
