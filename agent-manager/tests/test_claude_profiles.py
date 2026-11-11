"""Claude native profiles and exact retained resumes; no live provider required."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import uuid

from agent_manager.errors import Error
from agent_manager.manager import Manager
from agent_manager.profiles.operations import delete_profile, duplicate_profile, inspect_profile
from agent_manager.profiles.sessions import continue_session, execution_profiles, remember
from agent_manager.runtime.execution import launch_home
from agent_manager.runtime.native.claude_sessions import transcript, validate_arguments


class ClaudeProfileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.cli = self.base / 'claude'
        self.cli.write_text('fixture executable; never run')
        self.cli.chmod(0o700)
        self.config = self.base / 'providers.toml'
        self.config.write_text('[providers.claude]\nexecutable=' + json.dumps(str(self.cli)) + '\n'
            'native_home=' + json.dumps(str(self.base / 'native')) + '\n')
        self.manager = Manager('claude', self.config, self.base / 'state')
        self.manager.new_profile('work'); self.manager.new_profile('backup')
        self.home = self.manager.home('work')
        self.native_id = str(uuid.uuid4())
        folder = self.home / 'projects' / 'native-project-key'
        folder.mkdir(parents=True)
        self.path = folder / (self.native_id + '.jsonl')
        self.path.write_text('native transcript fixture; Manager never parses messages')
        self.saved = remember(self.manager, 'work', self.native_id, cwd=self.base)

    def test_native_login_runs_in_selected_home_without_reading_credentials(self):
        credential = self.home / '.credentials.json'
        credential.write_text('native-owned-placeholder')
        with patch.object(self.manager, '_invoke_native', return_value=0) as native:
            self.assertEqual(self.manager.login('work'), 0)
        self.assertEqual(native.call_args.args, (self.home, ['auth', 'login']))
        self.assertEqual(credential.read_text(), 'native-owned-placeholder')
        self.assertNotIn('native-owned-placeholder', self.manager.state_path.read_text())
        self.assertEqual(inspect_profile(self.manager, 'work')['login']['state'], 'unknown')

    def test_status_retains_only_public_observation(self):
        response = SimpleNamespace(returncode=0, stdout=json.dumps({'loggedIn': True,
            'email': 'private@example.test', 'accessToken': 'native-secret'}), stderr='native-private-stderr')
        with patch('agent_manager.environments.login.subprocess.run', return_value=response) as native:
            self.assertEqual(self.manager.login_status('work')['state'], 'signed-in')
        self.assertEqual(native.call_args.args[0], [str(self.cli), 'auth', 'status'])
        self.assertEqual(native.call_args.kwargs['env']['CLAUDE_CONFIG_DIR'], str(self.home))
        for private in ('private@example.test', 'native-secret', 'native-private-stderr'):
            self.assertNotIn(private, self.manager.state_path.read_text())

    def test_failed_status_cannot_claim_signed_in(self):
        for output, code, state in (('{"loggedIn":true}', 1, 'unknown'),
                                    ('{"loggedIn":false}', 1, 'signed-out'),
                                    ('invalid output', 0, 'unknown')):
            with self.subTest(output=output), patch('agent_manager.environments.login.subprocess.run',
                    return_value=SimpleNamespace(returncode=code, stdout=output)):
                self.assertEqual(self.manager.login_status('work')['state'], state)
        with patch('agent_manager.environments.login.subprocess.run', side_effect=OSError('private-error')):
            self.assertEqual(self.manager.login_status('work')['verification'], 'unavailable')
        self.assertNotIn('private-error', self.manager.state_path.read_text())

    def test_logout_and_login_refuse_active_managed_runs(self):
        with patch.object(self.manager, 'active_runs', return_value=[{'pid': os.getpid()}]), \
                patch.object(self.manager, '_invoke_native') as native:
            for operation in (self.manager.login, self.manager.logout):
                with self.assertRaisesRegex(Error, 'Stop this profile'):
                    operation('work')
        native.assert_not_called()

    def test_failed_logout_preserves_previous_observation(self):
        with patch('agent_manager.environments.login.subprocess.run',
                return_value=SimpleNamespace(returncode=0, stdout='{"loggedIn":true}')):
            self.manager.login_status('work')
        with patch.object(self.manager, '_invoke_native', return_value=1):
            self.assertEqual(self.manager.logout('work'), 1)
        self.assertEqual(inspect_profile(self.manager, 'work')['login']['state'], 'signed-in')
        with patch.object(self.manager, '_invoke_native', return_value=0) as native:
            self.assertEqual(self.manager.logout('work'), 0)
        self.assertEqual(native.call_args.args, (self.home, ['auth', 'logout']))
        self.assertEqual(inspect_profile(self.manager, 'work')['login']['state'], 'signed-out')

    def test_duplicate_and_disconnect_preserve_native_files_without_copying_them(self):
        (self.home / '.credentials.json').write_text('native-owned-placeholder')
        copied = duplicate_profile(self.manager, 'work', 'copy')
        self.assertNotEqual(copied['home'], str(self.home))
        self.assertFalse((Path(copied['home']) / '.credentials.json').exists())
        self.assertFalse((Path(copied['home']) / 'projects').exists())
        self.manager.rename('work', 'renamed')
        self.assertEqual(self.manager.home('renamed'), self.home)
        delete_profile(self.manager, 'renamed')
        self.assertTrue(self.path.is_file())
        self.assertEqual(self.manager.state()['sessions'][self.saved['ref']], self.saved)

    @unittest.skipUnless(sys.platform == 'linux', 'native file handoffs currently require Linux')
    def test_cross_environment_resume_advertises_managed_handoff(self):
        caps = inspect_profile(self.manager, 'work')['capabilities']
        self.assertTrue(caps['crossEnvironmentResume'])
        self.assertIsNone(caps['crossEnvironmentResumeReason'])
        self.assertEqual(execution_profiles(self.manager, self.saved), ['backup', 'work'])
        with patch('agent_manager.runtime.native.file_continuation.require_version'), patch.object(self.manager, '_invoke_native', return_value=0) as native:
            self.assertEqual(continue_session(self.manager, self.saved['ref'], profile='backup'), 0)
        self.assertEqual(native.call_args.args[0], self.manager.home('backup'))

    def test_original_home_can_resume_by_retained_reference(self):
        with patch.object(self.manager, '_invoke_native', return_value=0) as native:
            self.assertEqual(continue_session(self.manager, self.saved['ref']), 0)
        self.assertEqual(native.call_args.args[0], self.home)
        self.assertIn(self.native_id, native.call_args.args[1])
        self.assertIn('--resume', native.call_args.args[1])

    def test_missing_or_ambiguous_transcript_fails_before_launch(self):
        self.path.unlink()
        with patch.object(self.manager, '_run_native') as native:
            with self.assertRaisesRegex(Error, 'transcript is unavailable'):
                continue_session(self.manager, self.saved['ref'])
        native.assert_not_called()
        self.path.write_text('native fixture')
        other = self.home / 'projects' / 'other-project-key'; other.mkdir()
        (other / self.path.name).write_text('another native fixture')
        with self.assertRaisesRegex(Error, 'ambiguous'):
            continue_session(self.manager, self.saved['ref'])

    def test_transcript_lookup_reads_no_message_or_credential_contents(self):
        with patch.object(Path, 'open', side_effect=AssertionError('Unexpected content read')):
            self.assertEqual(transcript(self.home, self.native_id), self.path)
        for invalid in ('../other', '--resume', 'not-a-uuid'):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(Error, 'UUID'):
                transcript(self.home, invalid)

    @unittest.skipUnless(os.name == 'posix', 'symlink fixture requires POSIX')
    def test_transcript_cannot_escape_the_original_history_home(self):
        self.path.unlink()
        outside = self.base / 'external.jsonl'; outside.write_text('external fixture')
        self.path.symlink_to(outside)
        with self.assertRaisesRegex(Error, 'unavailable'):
            transcript(self.home, self.native_id)

    def test_provider_preferences_cannot_fork_or_replace_retained_resume(self):
        for flag in ('--fork-session', '--continue', '--session-id=private-option-value',
                     '--resume=another-session', '--bg', '--worktree', '--no-session-persistence', '-pc'):
            with self.subTest(flag=flag), launch_home(self.manager, 'work', cwd=self.base,
                    thread_id=self.native_id, source_session=self.saved):
                arguments = [str(self.cli), flag, '--resume', self.native_id]
                with self.assertRaisesRegex(Error, 'Claude continuation') as caught:
                    self.manager.launch_command(self.home, arguments)
                self.assertNotIn('private-option-value', str(caught.exception))

    def test_literal_prompt_values_and_model_overrides_are_preserved(self):
        arguments = [str(self.cli), '--append-system-prompt', '--fork-session',
            '--model=fixture-model', '--resume=' + self.native_id]
        with launch_home(self.manager, 'work', cwd=self.base, thread_id=self.native_id, source_session=self.saved):
            self.assertEqual(self.manager.launch_command(self.home, arguments), arguments)
        with self.assertRaisesRegex(Error, 'exact original'):
            validate_arguments([str(self.cli)], self.native_id)

    def test_final_native_dispatch_checks_inherited_fork_flags(self):
        self.manager.config['launch_args'] = ['--fork-session']
        with patch('agent_manager.manager.subprocess.call') as native:
            with self.assertRaisesRegex(Error, '--fork-session'):
                continue_session(self.manager, self.saved['ref'])
        native.assert_not_called()

    def test_managed_writer_lease_stays_bound_to_original_session(self):
        with launch_home(self.manager, 'work', cwd=self.base,
                thread_id=self.native_id, source_session=self.saved) as plan:
            state = self.manager.state()
            state['runs']['active'] = {'agent': 'work', 'environment_id': self.manager.resolve_profile('work').environment_id, 'pid': os.getpid(), 'home': str(self.home),
                'thread_id': self.native_id}
            with self.assertRaisesRegex(Error, 'active managed run'):
                plan.claim(self.manager, state, 'work')


if __name__ == '__main__':
    unittest.main()
