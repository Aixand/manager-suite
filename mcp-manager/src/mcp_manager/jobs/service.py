"""Retained preparation job intent, dispatch and status records."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from ..errors import McpError
from ..storage.files import locks, operational
from ..storage.platform import check_process
from ..storage.records import read, write
from ..library.definitions import digest, names
from ..preparation.options import options


def job_id(identifier):
    try:
        return str(uuid.UUID(identifier))
    except (ValueError, TypeError, AttributeError) as error:
        raise McpError("validation", "MCP preparation key must be a UUID") from error


def status(root, identifier):
    identifier = job_id(identifier)
    path = Path(root) / "jobs" / identifier / "status.json"
    row = read(path)
    if not row:
        raise McpError("not_found", "MCP preparation job not found")
    if row["status"] in ("queued", "running") and row.get("pid"):
        try:
            check_process(row["pid"])
        except ProcessLookupError:
            row.update(status="interrupted", error="Preparation worker stopped; the operation was not replayed")
        except PermissionError:
            pass
    return row


@operational
def start(manager, tools, project, *, refresh=False, settings=None, key=None, _managed=None):
    store = manager
    directory = store.context(Path.cwd(), project)
    if directory is None:
        raise McpError("validation", "Preparation requires an explicit project")
    if not isinstance(refresh, bool):
        raise McpError("validation", "MCP refresh must be a boolean")
    tools = names(tools)
    if _managed is None and (not tools or any(tool not in ("sonar", "lore") for tool in tools)):
        raise McpError("validation", "Preparation supports Sonar and Lore")
    settings = options(settings)
    identifier = job_id(key) if key is not None else str(uuid.uuid4())
    job_dir = store.root / "jobs" / identifier
    request = {"tools": tools, "project": str(directory), "refresh": refresh, "settings": settings}
    if _managed is not None:
        request["managed"] = _managed
    signature = digest(request)
    with locks(store.root / ".locks", "job-" + identifier, wait=True):
        existing = read(job_dir / "status.json")
        if existing:
            if existing["signature"] != signature:
                raise McpError("conflict", "MCP preparation key was reused with different arguments")
            return status(store.root, identifier)
        capture = None
        if _managed is not None:
            value = store.resolve_managed(_managed["publisher_id"], _managed["server_id"], revision=_managed["revision"])
            kind = value["definition"]["kind"]
            if kind not in ("sonar", "lore"):
                raise McpError("unsupported", "This MCP has no project preparation adapter")
            tools = [kind]
            request["tools"] = tools
            definitions = {kind: value["definition"]}
            capture = {"revision": value["resolution"]["revision"], "digest": value["digest"]}
        else:
            registry = store.registry()
            for tool in tools:
                if tool not in registry:
                    raise McpError("not_found", f"Unknown MCP: {tool}; register it before preparing its project index")
            definitions = {tool: registry[tool] for tool in tools}
        row = {"schema": 1, "id": identifier, "signature": signature, "status": "queued",
               "project": str(directory), "tools": tools, "step": "queued", "error": None,
               "created_at": time.time(), "updated_at": time.time()}
        write(job_dir / "request.json", request | {"definitions": definitions}
              | ({"managed_capture": capture} if capture is not None else {}))
        write(job_dir / "status.json", row)
        environment = os.environ | {"PYTHONPATH": str(Path(__file__).resolve().parents[2])
                                  + os.pathsep + os.environ.get("PYTHONPATH", "")}
        with (job_dir / "worker.log").open("ab") as log:
            try:
                worker = subprocess.Popen([sys.executable, "-m", "mcp_manager.jobs", str(store.root), identifier],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=environment,
                    start_new_session=os.name == "posix")
            except OSError:
                row.update(status="failed", error="Cannot start MCP preparation worker")
                write(job_dir / "status.json", row)
                raise McpError("io", row["error"]) from None
        row["pid"] = worker.pid
        write(job_dir / "status.json", row)
        # Reap while this caller lives; a detached worker survives caller exit.
        threading.Thread(target=worker.wait, daemon=True).start()
    return row


def update(root, identifier, **changes):
    with locks(Path(root) / ".locks", "job-" + identifier, wait=True):
        path = Path(root) / "jobs" / identifier / "status.json"
        row = read(path)
        row.update(changes, updated_at=time.time())
        write(path, row)


def run_step(root, identifier, label, command, report):
    update(root, identifier, status="running", step=label)
    with Path(report).open("wb") as output:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output, stderr=sys.stderr)
    if result.returncode:
        raise McpError("validation", f"{label} failed (exit {result.returncode}); inspect the preparation job log")


def listing(root, project=None):
    rows = []
    project = str(Path(project).expanduser().resolve()) if project else None
    for path in (Path(root) / "jobs").glob("*/status.json"):
        row = read(path)
        if project is None or row.get("project") == project:
            rows.append(status(root, row["id"]))
    return sorted(rows, key=lambda row: row["created_at"], reverse=True)[:50]
