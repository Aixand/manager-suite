"""Project context and selection resolution; no installation side effects."""
import hashlib
from pathlib import Path
import shutil

from ..errors import McpError
from ..storage.records import read
from ..diagnostics.stdio import probe
from .definitions import AUTO, PREFIX, PROJECT_KINDS, digest, names, preparation_digest

def project_path(manager, root, *, store=None):
    root = str(Path(root).expanduser().resolve())
    key = hashlib.sha256(root.encode()).hexdigest()
    return (manager.root if store is None else store) / "projects" / key



def context(manager, cwd, project=AUTO, *, store=None):
    if project is None:
        return None
    directory = Path(cwd if project is AUTO else project).expanduser().resolve()
    if not directory.is_dir():
        raise McpError("validation", "MCP project directory does not exist")
    if project is not AUTO:
        return directory
    for ancestor in (directory, *directory.parents):
        if (project_path(manager, ancestor, store=store) / "project.json").exists():
            return ancestor
        if (ancestor / ".git").exists():
            return ancestor
    return directory


def resolve(manager, servers, cwd, *, project=AUTO, strict=False, check=False, _definitions=None, _preparation_root=None):
    identifiers = names(servers)
    registry = (_definitions if _definitions is not None else manager.registry()) if identifiers else {}
    store = Path(_preparation_root).expanduser().resolve() if _preparation_root is not None else None
    root = (context(manager, cwd, project, store=store) if store is not None
            else manager.context(cwd, project)) if identifiers else None
    manifest = read(project_path(manager, root, store=store) / "project.json", {}) if root else {}
    servers, states = {}, []
    for key in identifiers:
        if key not in registry:
            raise McpError("not_found", f"Unknown MCP: {key}")
        row = dict(registry[key])
        native_name = PREFIX + key
        state = {"name": key, "server_name": native_name, "status": "configured", "project": str(root) if root else None}
        executable = shutil.which(row["command"])
        if row["kind"] != "stdio" and root is None:
            state["status"] = "needs-project"
        elif executable is None:
            state.update(status="missing-executable", error=f"MCP executable not found: {row['command']}")
        elif row["kind"] != "stdio":
            prepared = manifest.get("tools", {}).get(key)
            if not prepared or prepared.get("root") != str(root):
                state.update(status="needs-preparation", error=f"Prepare {key}: mcp-manager prepare {key} --project {root}")
            elif (prepared.get("preparation_digest") != preparation_digest(registry[key])
                  if "preparation_digest" in prepared else prepared.get("definition_digest") != digest(registry[key])):
                state.update(status="needs-preparation", error=f"{key} definition changed; refresh its project index")
            elif not Path(prepared["database"]).is_file():
                state.update(status="needs-preparation", error=f"{key} index is missing; prepare the project again")
            else:
                row["args"] = prepared["args"]
                state.update(status="prepared", generation=prepared["generation"])
        if state["status"] in ("configured", "prepared"):
            row["command"] = str(Path(executable).absolute())
            row["server_name"] = native_name
            row.setdefault("cwd", str(Path(cwd).expanduser().resolve()))
            servers[key] = row
        elif strict and state["status"] != "needs-project":
            raise McpError("validation", state["error"])
        states.append(state)
    if check:
        for state in states:
            row = servers.get(state["name"])
            if row and row["kind"] in PROJECT_KINDS:
                try:
                    result = probe(row, row["kind"] + "_status", {"check": True} if row["kind"] == "lore" else {})
                    if row["kind"] == "sonar" and not isinstance(result.get("index", {}), dict):
                        raise McpError("integrity", "MCP preset returned invalid freshness metadata")
                    state["freshness"] = ("stale" if result.get("needs_reindex") else "current") if row["kind"] == "lore" else result.get("index", {}).get("syntax_freshness", "unknown")
                except McpError as error:
                    state.update(freshness="unavailable", error=str(error))
    resolution = {"schema": "mcp-manager/resolution-1", "store": str(manager.root),
                  "store_id": manager.store_id, "cwd": str(Path(cwd).expanduser().resolve()),
                  "project": str(root) if root else None, "servers": servers, "states": states}
    return resolution | {"digest": digest(resolution)}


def inspect(manager, servers, cwd, *, project=AUTO):
    """Inspect independent selections without an unknown name hiding others.

    This is offline configuration/preparation evidence, never a claim that a
    server connected. Integrity and permission failures still propagate.
    """
    identifiers = names(servers)
    available, states, root = {}, [], None
    for identifier in identifiers:
        try:
            row = manager.resolve([identifier], cwd, project=project)
        except McpError as error:
            if error.code != "not_found":
                raise
            states.append({"name": identifier, "status": "not_found", "error": error.message})
        else:
            available.update(row["servers"])
            states.extend(row["states"])
            root = row["project"]
    row = {"schema": "mcp-manager/inspection-1", "store": str(manager.root),
           "store_id": manager.store_id, "cwd": str(Path(cwd).expanduser().resolve()),
           "project": root, "servers": available, "states": states}
    return row | {"digest": digest(row)}


def status(manager, project, *, servers=None, check=False):
    directory = Path.cwd() if project is None or project is AUTO else project
    if servers is None:
        servers = [key for key, row in manager.registry().items() if row["kind"] in PROJECT_KINDS]
    return manager.resolve(servers, directory,
                        project=project, check=check)
