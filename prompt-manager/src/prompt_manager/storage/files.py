"""Bounded JSON reads and atomic mutable-record writes."""

import os
import tempfile
from pathlib import Path

from ..codec import MAX_BYTES, canonical, parse
from ..errors import PromptError, require


def read_json(path):
    path = Path(path)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES, f"{path}: exceeds {MAX_BYTES} bytes", "size")
        return parse(raw, str(path))
    except OSError as exc:
        raise PromptError("io", f"Cannot read {path}: {exc.strerror}") from exc


def sync_directory(directory):
    """Persist directory entries on POSIX; Windows has no stdlib equivalent."""
    if os.name == "posix":
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def ensure_directory(directory, *, mode=0o700):
    """Create parents and synchronize newly introduced directory entries."""
    directory = Path(directory)
    missing = []
    parent = directory
    while not parent.exists():
        missing.append(parent)
        if parent == parent.parent:
            raise OSError(f"Cannot find an existing parent for {directory}")
        parent = parent.parent
    directory.mkdir(parents=True, exist_ok=True, mode=mode)
    for created in reversed(missing):
        sync_directory(created.parent)


def atomic_json(path, value):
    if path.is_file():
        try:
            if canonical(read_json(path)) == canonical(value):
                sync_directory(path.parent)
                return
        except ValueError:
            pass
    ensure_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".registry-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
