"""State commits survive brief Windows reader locks without losing old data."""
import errno
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp_manager import _storage as core


def windows_error(code):
    error = PermissionError(errno.EACCES, 'File is briefly in use')
    error.winerror = code
    return error


class AtomicTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'progress.json'
        self.path.write_bytes(b'previous progress')

    def test_transient_windows_reader_errors_preserve_old_data_until_commit(self):
        replace = os.replace
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                self.path.write_bytes(b'previous progress')
                attempts = []
                def locked_once(source, target):
                    attempts.append(source)
                    self.assertEqual(self.path.read_bytes(), b'previous progress')
                    if len(attempts) == 1:
                        raise windows_error(code)
                    replace(source, target)
                with patch.object(core.os, 'replace', side_effect=locked_once), patch.object(core.time, 'sleep'):
                    core.atomic(self.path, b'new progress')
                self.assertEqual(self.path.read_bytes(), b'new progress')
                self.assertEqual(len(attempts), 2)
                self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_persistent_windows_access_error_is_bounded_and_preserves_original(self):
        failure = windows_error(5)
        with patch.object(core.os, 'replace', side_effect=failure) as replace, \
                patch.object(core.time, 'monotonic', side_effect=[0, 0, 2]), \
                patch.object(core.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError) as caught:
                core.atomic(self.path, b'new progress')
        self.assertIs(caught.exception, failure)
        self.assertEqual(replace.call_count, 2)
        sleep.assert_called_once()
        self.assertEqual(self.path.read_bytes(), b'previous progress')
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_unrelated_permission_and_io_errors_are_not_retried(self):
        for failure in (PermissionError(errno.EACCES, 'No permission'), OSError(errno.ENOSPC, 'Disk full')):
            with self.subTest(error=failure):
                with patch.object(core.os, 'replace', side_effect=failure) as replace, patch.object(core.time, 'sleep') as sleep:
                    with self.assertRaises(OSError) as caught:
                        core.atomic(self.path, b'new progress')
                self.assertIs(caught.exception, failure)
                replace.assert_called_once()
                sleep.assert_not_called()
                self.assertEqual(self.path.read_bytes(), b'previous progress')
                self.assertEqual(list(self.root.iterdir()), [self.path])

    @unittest.skipUnless(os.name == 'nt', 'Requires Windows file sharing semantics')
    def test_real_windows_reader_can_close_before_progress_commit(self):
        reader = subprocess.Popen([sys.executable, '-I', '-B', '-c',
            'import sys,time; f=open(sys.argv[1],"rb"); print("locked",flush=True); time.sleep(.3); f.close()', str(self.path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(reader.stdout.readline().strip(), 'locked')
            core.atomic(self.path, b'new progress')
            stdout, stderr = reader.communicate(timeout=5)
            self.assertEqual(reader.returncode, 0, stdout + stderr)
            self.assertEqual(self.path.read_bytes(), b'new progress')
            self.assertEqual(list(self.root.iterdir()), [self.path])
        finally:
            if reader.poll() is None:
                reader.kill()
            reader.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
