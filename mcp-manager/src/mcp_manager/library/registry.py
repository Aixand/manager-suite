"""Local registry service with guarded, atomic partial configuration edits."""
from pathlib import Path
import shutil

from ..errors import McpError
from ..storage.files import locks, name, operational
from ..storage.records import read, write
from .definitions import definition, digest

LEGACY = object()


class Registry:
    def __init__(self, root):
        self.root = Path(root)

    def read(self):
        saved = read(self.root / "registry.json", {"schema": 1, "servers": {}})
        if not isinstance(saved, dict) or saved.get("schema") != 1 or not isinstance(saved.get("servers"), dict):
            raise McpError("validation", "Invalid MCP registry")
        return {key: definition(key, value) for key, value in saved["servers"].items()}

    @operational
    def configure(self, identifier, value, *, expected_revision, legacy=False):
        name(identifier)
        if not isinstance(value, dict):
            raise McpError("validation", "MCP definition must be an object")
        with locks(self.root / ".locks", "registry", wait=True):
            saved = read(self.root / "registry.json", {"schema": 1, "servers": {}})
            current = self.read().get(identifier)
            revision = digest(current) if current is not None else None
            if expected_revision is not LEGACY and expected_revision != revision:
                raise McpError("conflict", "MCP definition changed; refresh before saving")
            row = definition(identifier, (current or {}) | value)
            saved["servers"][identifier] = row
            write(self.root / "registry.json", saved)
            result = {"name": identifier, **row}
            return result if legacy else result | {"revision": digest(row)}

    def catalog(self):
        return [{"name": key, **value, "installed": shutil.which(value["command"]) is not None, "revision": digest(value)}
                for key, value in sorted(self.read().items())]

    def show(self, identifier):
        name(identifier)
        row = self.read().get(identifier)
        if row is None:
            raise McpError("not_found", "MCP definition not found")
        return {"name": identifier, **row, "installed": shutil.which(row["command"]) is not None,
                "revision": digest(row)}
