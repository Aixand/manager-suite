"""Immutable publisher artifacts captured into MCP Manager's owned library."""
import hashlib
import os
from pathlib import Path
import re

from ..errors import McpError
from ..storage.files import atomic, locks


def capture(manager, source, *, suffix=".pyz"):
    if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
        raise McpError("validation", "Invalid artifact suffix")
    if isinstance(source, bytes):
        data = source
    else:
        path = Path(source)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16*1024*1024:
            raise McpError("validation", "Artifact must be a bounded regular file")
        data = path.read_bytes()
    if not data or len(data) > 16*1024*1024:
        raise McpError("validation", "Artifact must contain 1–16 MiB of data")
    sha = hashlib.sha256(data).hexdigest()
    root = manager.root / "artifacts"
    if root.is_symlink():
        raise McpError("integrity", "Artifact directory must not be a symlink")
    with locks(manager.root / ".locks", "artifacts", wait=True):
        destination = root / (sha + suffix)
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink() or destination.read_bytes() != data:
                raise McpError("integrity", "Owned artifact content differs from its digest")
        else:
            atomic(destination, data)
            os.chmod(destination, 0o400)
    return {"schema": "mcp-manager/artifact-1", "digest": "sha256:" + sha, "path": str(destination), "size": len(data)}
