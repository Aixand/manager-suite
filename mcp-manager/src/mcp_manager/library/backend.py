"""Storage and installation-readiness adapter for owned MCP definitions."""
from copy import deepcopy
import json
from pathlib import Path
import re

from ..storage.codec import bounded, canonical, digest
from ..storage.files import atomic, locks
from ..errors import McpError, require

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def normalize(value):
    from .definitions import definition
    bounded(value)
    require(isinstance(value, dict), "MCP definition must be an object")
    kind = value.get("kind", "stdio")
    return bounded(definition(kind if kind in ("sonar", "lore") else "definition", value))


class Backend:
    schema = "mcp-manager/managed-1"
    errors = (McpError, OSError)

    def __init__(self, manager):
        self.manager, self.home = manager, manager.home

    def identifier(self, value):
        require(isinstance(value, str) and IDENTIFIER.fullmatch(value), "Invalid managed MCP identifier")

    def check_digest(self, value):
        require(isinstance(value, str) and DIGEST.fullmatch(value), "Invalid managed MCP digest", "integrity")

    def lock(self):
        return locks(self.home / ".locks", "managed", wait=True)

    def read(self, path, *, missing_ok=False):
        try:
            require(path.stat().st_size <= 262144, "Managed metadata exceeds size limit", "integrity")
            def pairs(items):
                result = {}
                for key, value in items:
                    require(key not in result, "Duplicate managed JSON key", "integrity")
                    result[key] = value
                return result
            return bounded(json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                parse_constant=lambda value: require(False, "Invalid managed JSON number", "integrity")))
        except FileNotFoundError:
            if missing_ok:
                return None
            raise McpError("not_found", "Managed MCP registration or snapshot not found") from None
        except (ValueError, UnicodeError) as error:
            raise McpError("integrity", "Invalid managed MCP metadata") from error
        except OSError as error:
            raise McpError("io", "Cannot read managed MCP metadata") from error

    def write(self, path, row, *, immutable=False):
        bounded(row)
        if immutable:
            existing = self.read(path, missing_ok=True)
            if existing is not None:
                require(canonical(existing) == canonical(row), "Immutable MCP object differs", "integrity")
                return
        atomic(path, canonical(row) + b"\n")

    def registrations(self):
        return [(path.parent.name, path.stem) for path in sorted((self.home / "managed").glob("*/*.json"))]

    def capture(self, source):
        if isinstance(source, (str, Path)):
            source = self.read(Path(source))
        return {"schema": "mcp-manager/definition-1", "definition": normalize(deepcopy(source))}

    def snapshot_digest(self, snapshot):
        return digest(snapshot)

    def store(self, snapshot):
        self.from_tree(snapshot)
        self.write(self.home / "objects" / (digest(snapshot)[7:] + ".json"), snapshot, immutable=True)

    def snapshot(self, identity):
        self.check_digest(identity)
        snapshot = self.read(self.home / "objects" / (identity[7:] + ".json"))
        require(digest(snapshot) == identity, "Managed MCP snapshot digest mismatch", "integrity")
        return self.from_tree(snapshot)

    def tree(self, snapshot):
        return deepcopy(snapshot)

    def from_tree(self, tree):
        require(isinstance(tree, dict) and set(tree) == {"schema", "definition"}
            and tree["schema"] == "mcp-manager/definition-1", "Invalid MCP definition snapshot", "integrity")
        result = {"schema": tree["schema"], "definition": normalize(tree["definition"])}
        require(result == tree, "MCP definition snapshot is not normalized", "integrity")
        return bounded(deepcopy(result))

    def edit(self, snapshot, value):
        return {"schema": "mcp-manager/definition-1", "definition": normalize(value)}


    def activation_ready(self, snapshot):
        from ..installations.service import ready
        return ready(self.home, snapshot)

    def status_details(self, row):
        from ..installations.service import status
        current = self.snapshot(row['active_digest'])['definition']
        incoming = self.snapshot(row['upstream']['digest'])['definition']
        return {'installation': status(self.home, current),
                'publisher_installation': status(self.home, incoming)}

