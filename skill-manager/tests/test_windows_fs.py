import os
from pathlib import Path
import tempfile
import unittest

from skill_manager import fs


@unittest.skipUnless(os.name == 'nt', 'Windows executable streams')
class WindowsStreamTests(unittest.TestCase):
    def test_long_atomic_paths_preserve_executable_streams_and_clean_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / ('a' * 60) / ('b' * 60) / ('c' * 40)
            os.makedirs('\\\\?\\' + str(parent))
            path = parent / 'tool.py'
            fs.write_atomic(path, b'first', executable=True)
            with fs.directory(parent) as directory:
                self.assertEqual(fs.read_at(directory, path.name), (b'first', True))
            fs.write_atomic(path, b'second', executable=False)
            self.assertFalse(fs.executable(path))
            immutable = parent / 'immutable.py'
            fs.write_atomic(immutable, b'fixed', executable=True, immutable=True)
            fs.write_atomic(immutable, b'fixed', executable=True, immutable=True)
            self.assertTrue(fs.executable(immutable))
            self.assertFalse(list(parent.glob('.skill-tmp-*')))
