"""Atomic immutable publication and content-addressed JSON objects."""

import os
import tempfile

from ..codec import MAX_BYTES, canonical, digest
from ..errors import require
from .files import ensure_directory, read_json, sync_directory


def publish_immutable(path, value):
    """Link a fully written file into place, never replacing an existing identity."""
    raw = canonical(value) + b"\n"
    require(len(raw) <= MAX_BYTES, "Stored object exceeds 32 MiB", "size")
    ensure_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            require(
                canonical(read_json(path)) == canonical(value),
                f"Immutable identity already contains different content: {path.name}",
                "conflict",
            )
        sync_directory(path.parent)
    finally:
        os.unlink(temporary)


def store_object(home, snapshot):
    identity = digest(snapshot)
    publish_immutable(home / "objects" / (identity[7:] + ".json"), snapshot)
    return identity


def load_object(home, identity):
    snapshot = read_json(home / "objects" / (identity[7:] + ".json"))
    require(digest(snapshot) == identity, "Managed snapshot digest mismatch", "integrity")
    return snapshot
