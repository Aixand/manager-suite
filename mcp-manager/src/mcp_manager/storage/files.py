"""Private atomic files and locks, preserving the on-disk protocol."""
import contextlib
import os
import re
import tempfile
import time
from ..errors import McpError
from .platform import lock_file


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise McpError("validation", f"Invalid name: {value!r}")
    return value


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def atomic(path, data):
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Status readers can briefly deny replacement on Windows. Keep the old
        # receipt intact while retrying; permanent and unrelated failures surface.
        deadline = time.monotonic() + 2
        while True:
            try:
                os.replace(temporary, path)
                break
            except OSError as error:
                if getattr(error, "winerror", None) not in (5, 32, 33) or time.monotonic() >= deadline:
                    raise
                time.sleep(.01)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def locks(root, *keys, wait=False):
    private_dir(root)
    with contextlib.ExitStack() as stack:
        for key in sorted(set(keys)):
            fd = os.open(root / (key + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
            stream = stack.enter_context(os.fdopen(fd, "a"))
            try:
                lock_file(stream, wait=wait)
            except BlockingIOError:
                raise McpError("busy", f"Busy: {key}; retry after the active operation finishes") from None
        yield


def operational(function):
    """Translate filesystem failures at public boundaries, preserving domain errors."""
    import functools

    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except OSError as error:
            raise McpError("io", "MCP store or executable is unavailable") from error
    return wrapped
