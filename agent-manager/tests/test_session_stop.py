"""Native stop verifies and releases a real OS writer lock."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests.test_codex_native_admission import NativeFixture
from agent_manager import Error
from agent_manager.runtime.native.session_stop import stop
from agent_manager.runtime.native.codex_continuation import writer_pid


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'pidfd_open'), 'Linux pidfds required')
class StopTests(NativeFixture, unittest.TestCase):
    def writer(self, name='codex'):
        program = self.base / name
        shutil.copyfile(shutil.which('sleep'), program)
        program.chmod(0o700)
        path = self.locks / (self.sid + '.lock')
        script = ('import fcntl,os,sys; f=open(sys.argv[1],"w"); '
                  'fcntl.flock(f,fcntl.LOCK_EX); os.set_inheritable(f.fileno(),True); '
                  'print("ready",flush=True); os.execv(sys.argv[2],[sys.argv[2],"60"])')
        child = subprocess.Popen([sys.executable, '-c', script, str(path), str(program)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(child.stdout.close)
        self.addCleanup(lambda: child.wait(timeout=5))
        self.addCleanup(lambda: child.kill() if child.poll() is None else None)
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        # Wait for exec, not just lock acquisition.
        import time
        deadline = time.monotonic() + 5
        while (Path('/proc') / str(child.pid) / 'exe').resolve().name != name:
            if time.monotonic() > deadline: self.fail('Child did not exec')
            time.sleep(.01)
        return child, path

    def test_stops_writer_preserves_lock_and_is_repeatable(self):
        child, path = self.writer()
        inode = path.stat().st_ino
        self.assertEqual(writer_pid(path), child.pid)
        result = stop(self.manager, self.sid)
        child.wait(timeout=5)
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(path.stat().st_ino, inode)
        self.assertIsNone(writer_pid(path))
        self.assertEqual(stop(self.manager, self.sid)['status'], 'already_closed')

    def test_refuses_non_codex_writer(self):
        child, path = self.writer('other')
        with self.assertRaisesRegex(Error, 'not a native Codex'):
            stop(self.manager, self.sid)
        self.assertIsNone(child.poll())
        self.assertEqual(writer_pid(path), child.pid)

    def test_changed_owner_is_not_signalled(self):
        child, path = self.writer()
        with patch('agent_manager.runtime.native.codex_continuation.writer_pid', side_effect=[child.pid, None]), patch('signal.pidfd_send_signal') as send:
            with self.assertRaisesRegex(Error, 'writer changed'):
                stop(self.manager, self.sid)
            send.assert_not_called()

    def test_closed_without_lock_file(self):
        self.assertEqual(stop(self.manager, self.sid)['status'], 'already_closed')

    def test_timeout_does_not_delete_lock_or_force_kill(self):
        child, path = self.writer()
        with patch('signal.pidfd_send_signal') as send:
            with self.assertRaisesRegex(Error, 'has not released'):
                stop(self.manager, self.sid, timeout=0)
            self.assertEqual(send.call_count, 1)
        self.assertEqual(writer_pid(path), child.pid)
        self.assertIsNone(child.poll())

    def test_cli_dispatch(self):
        from agent_manager.interfaces.cli.dispatch import execute
        import contextlib
        import io
        output = io.StringIO()
        with patch('agent_manager.interfaces.cli.dispatch.Manager', return_value=self.manager), contextlib.redirect_stdout(output):
            self.assertEqual(execute(['codex', 'stop', self.sid, '--json']), 0)
        import json
        self.assertEqual(json.loads(output.getvalue())['status'], 'already_closed')

    def test_rejects_symlink(self):
        (self.locks / (self.sid + '.lock')).symlink_to(self.base / 'other')
        with self.assertRaisesRegex(Error, 'symlinks'):
            stop(self.manager, self.sid)
