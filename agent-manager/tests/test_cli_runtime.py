from pathlib import Path
from contextlib import redirect_stderr
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import os

from tests import support as fixtures
from agent_manager.interfaces.cli import terminal as cli_runtime
from agent_manager.interfaces import sdk
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.interfaces.cli.dispatch import main
from agent_manager.errors import Error
from agent_manager.manager import Manager
from agent_manager.runtime.runs import finish_run, update_run


class RuntimeError(Exception):
    pass


class Runtime:
    connection_options = {"home": "/tmp/agent-manager-test-runtime", "bridge": "fake-bridge"}

    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass


class CLIRuntimeTests(unittest.TestCase):
    setUp = fixtures.ProfileFixture.setUp
    create = fixtures.ProfileFixture.create

    def test_cli_interrupt_exits_without_traceback(self):
        output = StringIO()
        with patch('agent_manager.interfaces.cli.dispatch.execute', side_effect=KeyboardInterrupt), redirect_stderr(output):
            with self.assertRaises(SystemExit) as result:
                main()
        self.assertEqual(result.exception.code, 130)
        self.assertEqual(output.getvalue(), '')

    def test_interrupted_direct_launch_releases_run_lease(self):
        self.create()
        with patch.object(self.manager, '_invoke_native', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.manager._run_native('work', [])
        self.assertFalse(self.manager.state()['runs'])

    def enable(self):
        self.config.write_text(self.config.read_text() + '\nruntime = "undercurrent"\n')
        self.manager = Manager('test', self.config, self.base / 'homes')

    def mock_runtime(self):
        self.enterContext(patch.object(cli_runtime, 'preflight'))
        self.enterContext(patch.object(cli_runtime, 'dependency', return_value=SimpleNamespace(
            Client=Runtime, UndercurrentError=RuntimeError)))
        self.enterContext(patch.object(sdk, 'launch_worker', side_effect=lambda manager, run_id:
            update_run(manager, run_id, session_id='session-' + run_id, status='running')))
        return self.enterContext(patch.object(cli_runtime.subprocess, 'call', return_value=0))

    def test_runtime_defaults_and_validation(self):
        self.assertEqual(self.manager.config['runtime'], 'direct')
        original = self.config.read_text()
        for value in ('"unknown"', 'true', '[]'):
            self.config.write_text(original + '\nruntime = ' + value + '\n')
            with self.assertRaisesRegex(Error, 'runtime must be'):
                Manager('test', self.config, self.base / 'homes')

    def test_cli_run_and_start_use_configured_runtime_and_preserve_launch(self):
        self.create()
        self.enable()
        self.config.write_text(self.config.read_text() + '\nlaunch_args = ["--model", "configured model"]\n')
        attach = self.mock_runtime()
        self.manager.set_default('work')
        for command in (['run', 'work'], ['start']):
            result = execute(['--config', str(self.config), '--home', str(self.base / 'homes'),
                              'test', *command, '--', 'prompt with spaces'])
            self.assertEqual(result, 0)
        self.assertEqual(attach.call_count, 2)
        runs = self.manager.state()['runs']
        self.assertEqual(len(runs), 2)
        for run_id, record in runs.items():
            self.assertEqual(record['runtime'], 'undercurrent')
            self.assertEqual(record['agent'], 'work')
            self.assertEqual(record['launch']['command'], [str(self.fake), '--configured', '--model',
                                                          'configured model', 'prompt with spaces'])
            self.assertEqual(record['launch']['env']['TEST_AGENT_HOME'], str(self.manager.home('work')))
            self.assertEqual(record['launch']['cwd'], str(Path.cwd()))
            self.assertEqual(record['launch']['env']['PWD'], str(Path.cwd().resolve()))
            self.assertEqual(record['launch']['idempotency_key'], run_id)
            self.assertRegex(record['launch']['name'], r'^work-[a-z]+-[a-z]+$')
            self.assertEqual(record['runtime_name'], record['launch']['name'])
            self.assertEqual(self.manager.state()['session_names'][record['runtime_name']], run_id)
        self.assertEqual(attach.call_args.args[0][:2], ['uc', 'attach'])
        self.assertIn(attach.call_args.args[0][2], {row['runtime_name'] for row in runs.values()})
        self.assertEqual(attach.call_args.kwargs['env']['UNDERCURRENT_HOME'], Runtime.connection_options['home'])
        self.assertFalse((self.manager.home('work') / 'observed.json').exists())

    def test_cli_context_merges_defaults_and_preserves_private_detail(self):
        self.create()
        self.enable()
        self.mock_runtime()
        with patch.dict(os.environ, AGENT_MANAGER_LABELS='{"app":"pathspec","role":"planner"}', AGENT_MANAGER_METADATA='{"private":"default"}'):
            execute(['--config', str(self.config), '--home', str(self.base / 'homes'), 'test',
                     'run', 'work', '--label', 'role=reviewer', '--metadata', 'private=launch'])
        record = next(iter(self.manager.state()['runs'].values()))
        self.assertEqual(record['launch']['labels']['app'], 'pathspec')
        self.assertEqual(record['launch']['labels']['role'], 'reviewer')
        self.assertEqual(record['launch']['metadata'], {'private': 'launch'})
        self.assertEqual(record['labels'], record['launch']['labels'])

    def test_direct_context_is_rejected_before_registry_changes(self):
        self.create()
        before = self.manager.state()
        with self.assertRaisesRegex(Error, 'undercurrent'):
            self.manager._run_native('work', [], labels={'app': 'pathspec'})
        self.assertEqual(self.manager.state(), before)

    def test_detach_and_finalization_preserve_native_home_and_profile_lease(self):
        self.create()
        self.enable()
        self.mock_runtime()
        self.manager._run_native('work', [])
        run_id = next(iter(self.manager.state()['runs']))
        home = self.manager.home('work')
        with patch('os.kill', side_effect=ProcessLookupError):
            self.assertEqual(len(self.manager.active_runs(self.manager.state(), 'work')), 1)
        self.manager.rename('work', 'renamed')
        self.assertEqual(self.manager.home('renamed'), home)
        marker = home / 'native-login-marker'; marker.write_text('provider-owned-update')
        finish_run(self.manager, run_id, status='finished')
        self.assertEqual(marker.read_text(), 'provider-owned-update')
        self.assertFalse(self.manager.state()['runs'])

    def test_missing_runtime_releases_unsubmitted_lease(self):
        self.create()
        self.enable()
        self.mock_runtime()
        with patch.object(Runtime, '__aenter__', side_effect=RuntimeError('daemon offline')):
            with self.assertRaisesRegex(Error, 'uc daemon start'):
                self.manager._run_native('work', [])
        self.assertFalse(self.manager.state()['runs'])

    def test_attachment_failure_keeps_running_session_recoverable(self):
        self.create()
        self.enable()
        attach = self.mock_runtime()
        attach.side_effect = OSError('attach failed')
        with self.assertRaisesRegex(OSError, 'attach failed'):
            self.manager._run_native('work', [])
        self.assertEqual(len(self.manager.state()['runs']), 1)


    def test_preflight_rejects_noninteractive_launch_before_registry_changes(self):
        self.create()
        self.enable()
        before = self.manager.state()
        with patch.object(cli_runtime.sys.stdin, 'isatty', return_value=False):
            with self.assertRaisesRegex(Error, 'interactive terminal'):
                self.manager._run_native('work', [])
        self.assertEqual(self.manager.state(), before)

    def test_login_still_runs_directly_with_undercurrent_configured(self):
        self.enable()
        with patch.object(cli_runtime, 'launch', side_effect=AssertionError('login must run directly')):
            self.create()
            self.manager.login('work', None)
        self.assertEqual((self.manager.home('work') / 'native-login-marker').read_text(), 'native-sign-in')
