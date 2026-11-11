"""Qualified same-home admission using Codex's exact native writer locks.

Unknown terminal identities remain unknown. A native idle check establishes only
that the selected conversation is not currently held; Codex acquires that same
writer lock at resume, closing races with other terminals sharing its daemon.
"""
from dataclasses import dataclass
from pathlib import Path
import sys

from agent_manager.errors import Error


def fingerprint(program):
    info = Path(program).stat()
    return [str(Path(program).resolve()), info.st_mtime_ns, info.st_size]


@dataclass(frozen=True)
class WriterGuard:
    home: Path
    thread_id: str
    program: list

    def validate(self, manager):
        from agent_manager.runtime.native.codex_continuation import _idle
        import shutil
        executable = shutil.which(manager.config['executable'])
        if executable is None or fingerprint(executable) != self.program:
            raise Error('Codex changed after its native writer protocol was qualified; retry selection')
        folder = self.home / 'thread-writer-locks'
        coordination = folder / '.coordination.lock'
        if folder.is_symlink() or not folder.is_dir() or coordination.is_symlink() or not coordination.is_file():
            raise Error('The native Codex writer-lock layout changed; continuation was refused')
        _idle(self.home, self.thread_id)

    def to_dict(self):
        return dict(home=str(self.home), thread_id=self.thread_id, program=self.program)


def prepare(manager, home, source):
    if sys.platform != 'linux' or Path(source['history_home']).resolve() != Path(home).resolve():
        return None
    folder = Path(home) / 'thread-writer-locks'
    if folder.is_symlink() or not folder.is_dir() or not (folder / '.coordination.lock').is_file():
        return None
    from agent_manager.runtime.native.codex_continuation import _native_version
    try:
        program = _native_version(manager)
    except Error:
        return None  # Preserve conservative admission for unqualified versions.
    guard = WriterGuard(Path(home).resolve(), source['native_id'], fingerprint(program))
    guard.validate(manager)
    return guard
