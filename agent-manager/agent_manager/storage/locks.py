"""Ordered filesystem leases shared by synchronous services and workers."""
import contextlib
import os
import time

from agent_manager.storage.platform import lock_file
from agent_manager.errors import BusyError
from agent_manager.storage.files import private_dir


@contextlib.contextmanager
def locks(root, *keys, wait=False, shared_keys=()):
    private_dir(root)
    with contextlib.ExitStack() as stack:
        for key in sorted(set(keys)):
            if os.name == 'nt' and key.startswith('environment-'):
                from agent_manager.storage.windows_guard import guard
                while True:
                    try:
                        stack.enter_context(guard(root / (key + '.lock'), shared=key in shared_keys))
                        break
                    except BlockingIOError:
                        if not wait:
                            raise BusyError(key) from None
                        time.sleep(.05)
                continue
            fd = os.open(root / (key + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
            stream = stack.enter_context(os.fdopen(fd, "a"))
            try:
                lock_file(stream, wait=wait, shared=key in shared_keys)
            except BlockingIOError:
                raise BusyError(key) from None
        yield


def shared_environment_keys(manager, environment_id):
    """Codex native readers and launches coexist with external process leases.

    Administrative changes continue to request exclusive environment locks.
    Other providers retain their existing exclusive launch policy.
    """
    if manager.provider == 'codex':
        return ('environment-' + environment_id,)
    return ()
