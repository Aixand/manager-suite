"""Copilot profile ownership and retained resumes without live credentials."""
import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from agent_manager.errors import Error
from agent_manager.manager import Manager
from agent_manager.profiles.operations import delete_profile, duplicate_profile, inspect_profile
from agent_manager.profiles.sessions import continue_session, execution_profiles, remember
from agent_manager.runtime.execution import launch_home
from agent_manager.runtime.native.copilot_sessions import event_log, validate_arguments


class CopilotProfileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.cli = self.base / 'copilot'
        self.cli.write_text('fixture executable; never run'); self.cli.chmod(0o700)
        config = self.base / 'providers.toml'
        config.write_text('[providers.copilot]\nexecutable=' + json.dumps(str(self.cli)) + '\n')
        self.manager = Manager('copilot', config, self.base / 'state')
        self.manager.new_profile('work'); self.manager.new_profile('backup')
        self.home = self.manager.home('work')
        self.native_id = str(uuid.uuid4())
        self.directory = self.home / 'session-state' / self.native_id
        self.directory.mkdir(parents=True)
        self.events = self.directory / 'events.jsonl'
        self.events.write_text('native history fixture; Manager never parses messages')
        self.workspace = self.directory / 'workspace.yaml'
        self.workspace.write_text('native workspace fixture')
        self.saved = remember(self.manager, 'work', self.native_id, cwd=self.base)

    def test_native_login_and_status_do_not_inspect_native_credentials(self):
        credential = self.home / 'config.json'; credential.write_text('native-owned-placeholder')
        with patch.object(self.manager, '_invoke_native', return_value=0) as native:
            self.assertEqual(self.manager.login('work'), 0)
        self.assertEqual(native.call_args.args, (self.home, ['login']))
        with patch('agent_manager.environments.login.subprocess.run') as native:
            self.assertEqual(self.manager.login_status('work'), {'state': 'unknown', 'verification': 'unsupported'})
        native.assert_not_called()
        with self.assertRaisesRegex(Error, 'interactive logout'):
            self.manager.logout('work')
        self.assertEqual(credential.read_text(), 'native-owned-placeholder')
        self.assertNotIn('native-owned-placeholder', self.manager.state_path.read_text())

    def test_ambient_provider_overrides_cannot_replace_native_profile_configuration(self):
        competing = {name: 'private-placeholder' for name in ('COPILOT_PROVIDER_BASE_URL',
            'COPILOT_PROVIDER_TYPE', 'COPILOT_PROVIDER_API_KEY', 'COPILOT_PROVIDER_API_KEY_COMMAND',
            'COPILOT_PROVIDER_BEARER_TOKEN', 'COPILOT_PROVIDER_HEADERS', 'COPILOT_PROVIDER_WIRE_API',
            'COPILOT_PROVIDER_WIRE_MODEL', 'COPILOT_PROVIDER_TRANSPORT', 'COPILOT_PROVIDER_MODEL_ID',
            'COPILOT_PROVIDER_MAX_PROMPT_TOKENS', 'COPILOT_PROVIDER_MAX_OUTPUT_TOKENS',
            'COPILOT_PROVIDER_AZURE_API_VERSION', 'COPILOT_PROVIDERS_CONFIG', 'GH_HOST', 'COPILOT_GH_HOST',
            'GH_TOKEN', 'GITHUB_TOKEN', 'COPILOT_GITHUB_TOKEN', 'COPILOT_SDK_AUTH_TOKEN')}
        registry = self.home / 'providers.json'; registry.write_text('native provider registry placeholder')
        with patch.dict(os.environ, competing | {'COPILOT_HOME': '/wrong/home', 'COPILOT_MODEL': 'chosen',
                'COPILOT_AUTO_UPDATE': 'false'}):
            env = self.manager.launch_environment(self.home)
        for name in competing: self.assertNotIn(name, env)
        self.assertEqual(env['COPILOT_HOME'], str(self.home))
        self.assertEqual(env['COPILOT_MODEL'], 'chosen')
        self.assertEqual(env['COPILOT_AUTO_UPDATE'], 'false')
        self.assertEqual(registry.read_text(), 'native provider registry placeholder')
        self.assertNotIn('private-placeholder', self.manager.state_path.read_text())

    def test_duplicate_and_disconnect_leave_login_and_history_provider_owned(self):
        (self.home / 'config.json').write_text('native-owned-placeholder')
        copied = Path(duplicate_profile(self.manager, 'work', 'copy')['home'])
        self.assertFalse((copied / 'config.json').exists())
        self.assertFalse((copied / 'session-state').exists())
        delete_profile(self.manager, 'work')
        self.assertTrue(self.events.is_file())
        self.manager.connect_profile('reconnected', self.home)
        with patch.object(self.manager, '_invoke_native', return_value=0) as native:
            self.assertEqual(continue_session(self.manager, self.saved['ref'], profile='reconnected'), 0)
        self.assertEqual(native.call_args.args[0], self.home)

    @unittest.skipUnless(sys.platform == 'linux', 'native file handoffs currently require Linux')
    def test_cross_environment_resume_advertises_managed_handoff(self):
        caps = inspect_profile(self.manager, 'work')['capabilities']
        self.assertTrue(caps['crossEnvironmentResume'])
        self.assertIsNone(caps['crossEnvironmentResumeReason'])
        self.assertEqual(execution_profiles(self.manager, self.saved), ['backup', 'work'])
        with patch('agent_manager.runtime.native.file_continuation.require_version'), patch.object(self.manager, '_invoke_native', return_value=0) as native:
            self.assertEqual(continue_session(self.manager, self.saved['ref'], profile='backup'), 0)
        self.assertEqual(native.call_args.args[0], self.manager.home('backup'))

    def test_same_home_resume_uses_exact_session_despite_changed_default(self):
        self.manager.set_default('backup')
        with patch.object(self.manager, '_invoke_native', return_value=0) as native:
            self.assertEqual(continue_session(self.manager, self.saved['ref']), 0)
        self.assertEqual(native.call_args.args[0], self.home)
        self.assertEqual(native.call_args.args[1][-2:], ['--resume', self.native_id])

    def test_missing_history_or_workspace_fails_before_native_launch(self):
        for path in (self.events, self.workspace):
            with self.subTest(path=path.name):
                path.unlink()
                with patch.object(self.manager, '_run_native') as native:
                    with self.assertRaisesRegex(Error, 'history or workspace is unavailable'):
                        continue_session(self.manager, self.saved['ref'])
                native.assert_not_called()
                path.write_text('native fixture')

    def test_lookup_only_checks_paths_and_requires_an_exact_uuid(self):
        with patch.object(Path, 'open', side_effect=AssertionError('Unexpected content read')):
            self.assertEqual(event_log(self.home, self.native_id), self.events)
        for invalid in ('../other', self.native_id[:7], 'friendly-name', self.native_id.replace('-', '')):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(Error, 'exact native session UUID'):
                event_log(self.home, invalid)

    @unittest.skipUnless(os.name == 'posix', 'symlink fixtures require POSIX')
    def test_native_history_and_workspace_cannot_alias_another_session(self):
        outside = self.base / 'external'; outside.write_text('external fixture')
        for path in (self.events, self.workspace):
            with self.subTest(path=path.name):
                path.unlink(); path.symlink_to(outside)
                with self.assertRaisesRegex(Error, 'unavailable'):
                    event_log(self.home, self.native_id)
                path.unlink(); path.write_text('native fixture')
        self.directory.rename(self.directory.with_name('elsewhere'))
        self.directory.symlink_to(self.directory.with_name('elsewhere'), target_is_directory=True)
        with self.assertRaisesRegex(Error, 'unavailable'):
            event_log(self.home, self.native_id)

    def test_final_command_rejects_inherited_session_home_and_workspace_overrides(self):
        for flag in ('--continue', '--session-id=private-option-value', '--connect', '--config-dir=elsewhere',
                     '--worktree', '-Celsewhere', '--resume=another-session', '--acp', '--headless',
                     '--auth-token-env=PRIVATE', '--', 'app'):
            with self.subTest(flag=flag), launch_home(self.manager, 'work', cwd=self.base,
                    thread_id=self.native_id, source_session=self.saved):
                with self.assertRaisesRegex(Error, 'Copilot continuation') as caught:
                    self.manager.launch_command(self.home, [str(self.cli), flag, '--resume', self.native_id])
                self.assertNotIn('private-option-value', str(caught.exception))

    def test_scalar_literals_and_permission_lists_do_not_change_the_retained_session(self):
        arguments = [str(self.cli), '--prompt', '--continue', '--model=fixture-model',
            '--available-tools', 'read_file', 'help', '--sync-session=off', '--resume=' + self.native_id]
        with launch_home(self.manager, 'work', cwd=self.base, thread_id=self.native_id, source_session=self.saved):
            self.assertEqual(self.manager.launch_command(self.home, arguments), arguments)
        for arguments in ([str(self.cli)], [str(self.cli), '-r', self.native_id, '--resume', self.native_id]):
            with self.assertRaisesRegex(Error, 'exact original native session once'):
                validate_arguments(arguments, self.native_id)

    def test_provider_launch_preferences_are_checked_at_final_native_dispatch(self):
        self.manager.config['launch_args'] = ['--config-dir=private-option-value']
        with patch('agent_manager.manager.subprocess.call') as native:
            with self.assertRaisesRegex(Error, '--config-dir'):
                continue_session(self.manager, self.saved['ref'])
        native.assert_not_called()

    def test_existing_managed_writer_remains_bound_to_source_session(self):
        with launch_home(self.manager, 'work', cwd=self.base,
                thread_id=self.native_id, source_session=self.saved) as plan:
            state = self.manager.state()
            state['runs']['active'] = {'agent': 'work', 'environment_id': self.manager.resolve_profile('work').environment_id, 'pid': os.getpid(), 'home': str(self.home), 'thread_id': self.native_id}
            with self.assertRaisesRegex(Error, 'active managed run'):
                plan.claim(self.manager, state, 'work')
