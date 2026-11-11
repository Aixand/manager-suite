"""Exercise real OS locks and process probes on Windows and POSIX."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_manager.storage.platform import check_process, lock_file
from agent_manager.errors import Error
from agent_manager.manager import Manager
from agent_manager.storage.locks import locks


PROJECT = Path(__file__).resolve().parents[1]


class PlatformTests(unittest.TestCase):
    def test_live_and_exited_process_probe_never_stops_child(self):
        child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                                 stdin=subprocess.PIPE)
        try:
            for _ in range(3):
                check_process(child.pid)
                self.assertIsNone(child.poll())
            from agent_manager.profiles.identities import empty
            state = empty()
            identifier = '11111111-1111-1111-1111-111111111111'
            state['environments'][identifier] = dict(home='/tmp/disposable-env', ownership='external', created_at=0)
            state['profiles']['work'] = dict(id='22222222-2222-2222-2222-222222222222', environment_id=identifier, created_at=0)
            state['runs']['live'] = dict(pid=child.pid, agent='work', environment_id=identifier)
            self.assertEqual(len(Manager.active_runs(state, 'work')), 1)
            self.assertIsNone(child.poll())
            child.communicate(timeout=5)
            with self.assertRaises(ProcessLookupError):
                check_process(child.pid)
            self.assertEqual(Manager.active_runs(state, 'work'), [])
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate()

    def test_nonblocking_lock_excludes_other_process_and_releases_on_close(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'busy.lock'
            script = '''
import sys
from agent_manager.storage.platform import lock_file
with open(sys.argv[1], 'a') as stream:
    try:
        lock_file(stream, wait=False)
    except BlockingIOError:
        sys.exit(7)
'''
            def probe():
                return subprocess.run([sys.executable, '-c', script, str(path)],
                                      cwd=PROJECT, timeout=5).returncode
            with path.open('a') as stream:
                lock_file(stream)
                self.assertEqual(probe(), 7)
                with self.assertRaisesRegex(Error, 'Busy: busy'):
                    with locks(Path(directory), 'busy'):
                        pass
            self.assertEqual(probe(), 0)

    def test_waiting_lock_acquires_after_owner_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wait.lock'
            script = '''
import sys
from agent_manager.storage.platform import lock_file
with open(sys.argv[1], 'a') as stream:
    print('ready', flush=True)
    lock_file(stream)
    print('acquired', flush=True)
'''
            with path.open('a') as stream:
                lock_file(stream)
                child = subprocess.Popen([sys.executable, '-c', script, str(path)],
                                         cwd=PROJECT, stdout=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(child.stdout.readline().strip(), 'ready')
                    with self.assertRaises(subprocess.TimeoutExpired):
                        child.wait(timeout=0.2)
                except BaseException:
                    child.kill()
                    child.communicate()
                    raise
            try:
                output, _ = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0)
                self.assertEqual(output.strip(), 'acquired')
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate()
