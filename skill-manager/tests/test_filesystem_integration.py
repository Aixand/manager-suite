import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import skill_manager
from skill_manager import Manager


class FilesystemIntegrationTests(unittest.TestCase):
    def test_store_uses_same_lock_file_and_excludes_legacy_protocol(self):
        # Deliberately does not import aixand-platform: this is its existing
        # flock/byte-zero protocol, usable in a standalone installed-wheel check.
        legacy = r"""
import errno, os, sys
with open(sys.argv[1], 'a+') as stream:
    try:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0, 2)
            if not stream.tell():
                stream.write('\0'); stream.flush()
            stream.seek(0)
            try: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise BlockingIOError() from error
                raise
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(17)
"""
        with tempfile.TemporaryDirectory() as temporary:
            manager = Manager(Path(temporary) / "store")
            path = manager.home / ".locks/store.lock"
            with manager._store.lock():
                self.assertTrue(path.is_file())
                identity = path.stat().st_ino
                result = subprocess.run(
                    [sys.executable, "-I", "-S", "-c", legacy, str(path)], timeout=10
                )
                self.assertEqual(17, result.returncode)
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-c", legacy, str(path)], timeout=10
            )
            self.assertEqual(0, result.returncode)
            with manager._store.lock():
                self.assertEqual(identity, path.stat().st_ino)

    def test_skill_runtime_has_no_monolithic_platform_import(self):
        for path in Path(skill_manager.__file__).parent.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else (
                        [node.module]
                        if isinstance(node, ast.ImportFrom) and node.module
                        else []
                    )
                )
                self.assertFalse(
                    any(name.split(".")[0] == "aixand_platform" for name in names),
                    str(path),
                )
