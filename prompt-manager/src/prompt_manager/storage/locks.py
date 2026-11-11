"""Cross-process store coordination."""

import os
from contextlib import contextmanager
from pathlib import Path

from ..codec import digest
from .files import ensure_directory


@contextmanager
def lock(home, identity):
    directory = Path(home) / "locks"
    ensure_directory(directory)
    with (directory / (digest(identity)[7:] + ".lock")).open("a+b") as stream:
        acquire(stream)
        try:
            yield
        finally:
            release(stream)


def acquire(stream):
    if os.name == "nt":
        import msvcrt

        # Append mode must not grow the lock file on every acquisition.
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(stream, fcntl.LOCK_EX)


def release(stream):
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream, fcntl.LOCK_UN)
