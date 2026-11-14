"""Stop a verified native Codex writer without removing its lock file."""
import os
from pathlib import Path
import signal
import sys
import time
import uuid

from agent_manager.errors import Error


def stop(manager, value, *, timeout=10):
    if manager.provider != 'codex' or sys.platform != 'linux':
        raise Error('Native session stop currently requires Codex on Linux')
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise Error('Native session stop requires Linux process identity handles')
    from agent_manager.profiles.sessions import find
    from agent_manager.runtime.native.codex_continuation import writer_pid, _idle
    saved = find(manager, value)
    thread_id = str(uuid.UUID(saved['native_id']))
    home = Path(saved['history_home'])
    path = home / 'thread-writer-locks' / (thread_id + '.lock')
    if path.parent.is_symlink() or path.is_symlink():
        raise Error('Codex writer locks must not be symlinks')
    result = dict(session=saved['ref'], native_id=thread_id, status='already_closed')
    pid = writer_pid(path)
    if pid is None:
        if path.exists(): _idle(home, thread_id)
        return result
    # A pidfd pins process identity even if the numeric PID is later reused.
    try:
        handle = os.pidfd_open(pid)
    except ProcessLookupError:
        _idle(home, thread_id)
        return result
    try:
        process = Path('/proc') / str(pid)
        if process.stat().st_uid != os.getuid():
            raise Error('Refusing to stop a Codex writer owned by another user')
        executable = (process / 'exe').resolve().name
        if executable != 'codex':
            raise Error('Refusing to stop a writer that is not a native Codex executable')
        if writer_pid(path) != pid:
            raise Error('Conversation writer changed; retry stop')
        signal.pidfd_send_signal(handle, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while True:
            try:
                _idle(home, thread_id, timeout=0)
                return dict(result, status='stopped', pid=pid)
            except Error:
                if time.monotonic() >= deadline:
                    raise Error('Codex has not released the conversation lock; exit its terminal and retry') from None
                time.sleep(.05)
    finally:
        os.close(handle)
