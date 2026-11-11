"""Exact native lock evidence narrows only qualified same-home admission."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

from agent_manager import Error, Manager
from agent_manager.profiles.sessions import remember
from agent_manager.runtime.execution import launch_home
from agent_manager.runtime.native.codex_admission import prepare


@unittest.skipUnless(sys.platform == 'linux', 'Qualified native writer locks require Linux')
class NativeFixture:
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.program = self.base / 'codex'
        self.program.write_text('#!' + sys.executable + '\nprint("codex-cli 0.166.0")\n')
        self.program.chmod(0o700)
        config = self.base / 'providers.toml'
        config.write_text('[providers.codex]\nexecutable=' + json.dumps(str(self.program)) + '\n')
        self.manager = Manager('codex', config=config, root=self.base / 'state')
        self.manager.new_profile('main')
        self.home = self.manager.home('main')
        self.sid = str(uuid.uuid4())
        (self.home / 'sessions').mkdir()
        (self.home / 'sessions' / 'original.jsonl').write_text(json.dumps({'type': 'session_meta',
            'payload': {'id': self.sid, 'cwd': str(self.base)}}) + '\n')
        self.locks = self.home / 'thread-writer-locks'
        self.locks.mkdir()
        (self.locks / '.coordination.lock').touch()
        self.saved = remember(self.manager, 'main', self.sid, cwd=self.base)

    def plan(self):
        return launch_home(self.manager, 'main', args=['resume', self.sid], cwd=self.base,
                           thread_id=self.sid, source_session=self.saved)

    def busy(self, thread_id=None):
        state = self.manager.state()
        target = self.manager.resolve_profile('main')
        state['runs']['other'] = dict(agent='main', environment_id=target.environment_id,
            home=str(self.home), pid=os.getpid(), thread_id=thread_id)
        self.manager.save(state)



@unittest.skipUnless(sys.platform == 'linux', 'Qualified native writer locks require Linux')
class NativeAdmissionTests(NativeFixture, unittest.TestCase):
    def test_unknown_terminal_does_not_block_a_proven_idle_native_conversation(self):
        self.busy()
        with self.plan() as plan:
            self.assertIsNotNone(plan.admission)
            plan.claim(self.manager, self.manager.state(), 'main')
            self.assertEqual(plan.provenance['native_writer_guard']['thread_id'], self.sid)
        self.assertIn('other', self.manager.state()['runs'])

    def test_known_writer_still_blocks_even_before_native_process_acquires_lock(self):
        self.busy(self.sid)
        with self.plan() as plan, self.assertRaisesRegex(Error, 'active managed run'):
            plan.claim(self.manager, self.manager.state(), 'main')

    def test_native_writer_blocks_even_without_a_registry_record(self):
        import fcntl
        with (self.locks / (self.sid + '.lock')).open('w') as writer:
            fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(Error, 'still open in another Codex process'):
                with self.plan():
                    self.fail('Native writer must prevent admission')

    def test_coordination_contention_is_distinct_and_bounded(self):
        import fcntl
        from agent_manager.runtime.native.codex_continuation import _idle
        with (self.locks / '.coordination.lock').open('rb') as coordination:
            fcntl.flock(coordination, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(Error, 'coordination is busy'):
                _idle(self.home, self.sid, timeout=0)

    def test_transient_coordination_contention_is_retried(self):
        import fcntl
        from unittest.mock import patch
        from agent_manager.runtime.native.codex_continuation import _idle
        real_flock = fcntl.flock
        calls = []
        def contend_once(fd, operation):
            calls.append(operation)
            if len(calls) == 1:
                raise BlockingIOError()
            return real_flock(fd, operation)
        with patch('fcntl.flock', side_effect=contend_once), patch('time.sleep'):
            _idle(self.home, self.sid)
        self.assertEqual(len(calls), 3)

    def test_busy_writer_diagnostic_identifies_owner(self):
        import fcntl
        from agent_manager.runtime.native.codex_continuation import _idle
        with (self.locks / (self.sid + '.lock')).open('w') as writer:
            fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(Error, 'writer PID ' + str(os.getpid())):
                _idle(self.home, self.sid)

    def test_missing_lock_protocol_preserves_unknown_writer_refusal(self):
        self.busy()
        (self.locks / '.coordination.lock').unlink()
        with self.plan() as plan, self.assertRaisesRegex(Error, 'active managed run'):
            self.assertIsNone(plan.admission)
            plan.claim(self.manager, self.manager.state(), 'main')

    def test_unqualified_binary_preserves_unknown_writer_refusal(self):
        self.busy()
        self.program.write_text('#!' + sys.executable + '\nprint("codex-cli 99.0.0")\n')
        with self.plan() as plan, self.assertRaisesRegex(Error, 'active managed run'):
            self.assertIsNone(plan.admission)
            plan.claim(self.manager, self.manager.state(), 'main')

    def test_binary_change_between_preparation_and_claim_is_rejected(self):
        with self.plan() as plan:
            self.program.write_text(self.program.read_text() + '# changed\n')
            with self.assertRaisesRegex(Error, 'Codex changed'):
                plan.claim(self.manager, self.manager.state(), 'main')

    def test_symlinked_coordination_lock_is_rejected(self):
        path = self.locks / '.coordination.lock'
        path.unlink()
        target = self.base / 'another-lock'
        target.touch()
        path.symlink_to(target)
        with self.assertRaisesRegex(Error, 'writer-lock layout changed'):
            with self.plan():
                self.fail('Symlinked native lock must not be trusted')

    def test_cross_home_launch_does_not_receive_local_admission_evidence(self):
        self.manager.new_profile('other')
        self.assertIsNone(prepare(self.manager, self.manager.home('other'), self.saved))

    def test_submission_rechecks_native_writer_and_other_prepared_runs(self):
        from agent_manager.runtime.continuation.codex import CodexAdapter
        target = self.manager.resolve_profile('main')
        with self.plan() as plan:
            record = dict(plan.provenance, home=str(self.home), environment_id=target.environment_id,
                          thread_id=self.sid)
        # Another writer can appear after preparation and before runtime submission.
        self.busy(self.sid)
        with self.assertRaisesRegex(Error, 'active managed run'):
            CodexAdapter().validate_submission(self.manager, record, 'own')
        self.busy()
        import fcntl
        with (self.locks / (self.sid + '.lock')).open('w') as writer:
            fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(Error, 'still open in another Codex process'):
                CodexAdapter().validate_submission(self.manager, record, 'own')


class ManagedCodexLaunchTests(unittest.TestCase):
    def manager(self):
        from types import SimpleNamespace
        return SimpleNamespace(provider='codex', provider_spec=SimpleNamespace(home=SimpleNamespace(opencode=False)))

    def test_only_managed_terminals_get_owned_backend_lifetime(self):
        from agent_manager.runtime.native.launch import terminal_launch
        arguments = ['--yolo', 'resume', 'saved-thread']
        self.assertEqual(terminal_launch(self.manager(), arguments, {})[0], arguments)
        managed, _ = terminal_launch(self.manager(), arguments, {}, managed=True)
        self.assertEqual(managed, ['--no-daemon', *arguments])
        self.assertEqual(arguments, ['--yolo', 'resume', 'saved-thread'])

    def test_owned_backend_flag_is_not_duplicated_or_confused_with_prompt_text(self):
        from agent_manager.runtime.native.launch import terminal_launch
        managed, _ = terminal_launch(self.manager(), ['--no-daemon'], {}, managed=True)
        self.assertEqual(managed.count('--no-daemon'), 1)
        managed, _ = terminal_launch(self.manager(), ['--', '--no-daemon'], {}, managed=True)
        self.assertEqual(managed, ['--no-daemon', '--', '--no-daemon'])

    def test_remote_attachment_cannot_claim_managed_backend_ownership(self):
        from agent_manager.runtime.native.launch import terminal_launch
        for arguments in (['--remote', 'unix:///other'], ['--remote=unix:///other']):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(Error, 'local owned backend'):
                terminal_launch(self.manager(), arguments, {}, managed=True)


@unittest.skipUnless(sys.platform == 'linux', 'Native executable fixture requires Linux')
class SDKNativeOwnershipTests(NativeFixture, unittest.IsolatedAsyncioTestCase):
    async def test_sdk_launch_records_the_owned_backend_command(self):
        from unittest.mock import patch
        from agent_manager import Client
        from agent_manager.runtime.runs import read_run, update_run
        from tests.support import FakeRuntime

        def accepted(manager, run_id):
            update_run(manager, run_id, session_id='owned-session', status='running')

        client = Client('codex', config=self.manager.config_path, root=self.base / 'state', runtime=FakeRuntime())
        with patch('agent_manager.interfaces.sdk.launch_worker', side_effect=accepted):
            agent = await client.spawn(profile='main', cwd=self.base)
        command = read_run(self.manager, agent.run_id)['launch']['command']
        self.assertIn('--no-daemon', command)
