"""Retained host-local jobs. An interrupted request is inspected, never replayed."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from ..sources.records import SourceError, atomic, canonical, digest, read, require
from ..storage.process import identity as process_identity

OPERATIONS = {
    "import-local": {"path"},
    "switch-source": {"publisher", "identifier", "expected_revision", "ref", "track"},
    "import-github": {"url", "ref", "path", "track", "selections", "role"},
    "discover": {"url", "ref", "path", "track"},
    "import": {"discovery_id", "selections", "role"},
    "check": {"publisher", "identifier", "expected_revision", "force"},
    "create": {
        "name",
        "content",
        "description",
        "role",
        "definition",
        "publisher",
        "resource_id",
    },
    "install": {
        "publisher",
        "identifier",
        "expected_revision",
        "candidate",
        "resolutions",
        "take",
    },
}


def key(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise SourceError("validation", "Job key must be a UUID") from None


def directory(sources, job_id):
    return sources.home / "source-jobs" / key(job_id)


def status(sources, job_id):
    path = directory(sources, job_id) / "status.json"
    require(path.is_file(), "Source job not found", "not_found")
    with sources.backend.lock():
        row = read(path)
        require(row is not None, "Source job not found", "not_found")
        if row["status"] in ("queued", "running") and row.get("pid"):
            try:
                if os.name != "nt":
                    os.kill(row["pid"], 0)
                if (
                    not row.get("process_identity")
                    or process_identity(row["pid"]) != row["process_identity"]
                ):
                    raise ProcessLookupError()
            except ProcessLookupError:
                row = {
                    **row,
                    "status": "interrupted",
                    "updated_at": time.time(),
                    "error": {
                        "code": "interrupted",
                        "message": "Worker stopped; this request was not replayed",
                    },
                }
                atomic(path, row)
            except PermissionError:
                pass
        return row


def listing(sources):
    home = sources.home
    paths = sorted(
        (home / "source-jobs").glob("*/status.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [status(sources, p.parent.name) for p in paths[:100]]


def start(sources, operation, request, *, key=None):
    require(
        operation in OPERATIONS
        and isinstance(request, dict)
        and not set(request) - OPERATIONS[operation],
        "Unsupported source job request",
    )
    require(
        len(canonical(request)) <= 1024 * 1024, "Source job request exceeds size limit"
    )
    identifier = globals()["key"](key) if key else str(uuid.uuid4())
    path = directory(sources, identifier)
    signature = digest({"operation": operation, "request": request})
    backend = sources.backend
    existing = read(path / "status.json")
    if existing:
        require(
            existing["signature"] == signature,
            "Job key reused with different arguments",
            "conflict",
        )
        return status(sources, identifier)
    with backend.lock():
        existing = read(path / "status.json")
        if existing:
            require(
                existing["signature"] == signature,
                "Job key reused with different arguments",
                "conflict",
            )
            return existing
        if operation == "install":
            from ..installations.service import capture_install

            capture = capture_install(sources.engine, **request)
        else:
            capture = None
        row = {
            "schema": "resource-sources/job-1",
            "id": identifier,
            "signature": signature,
            "operation": operation,
            "status": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": None,
            "error": None,
            "pid": None,
            "step": "queued",
        }
        atomic(
            path / "request.json",
            {"operation": operation, "request": request, "capture": capture},
        )
        atomic(path / "status.json", row)
        with (path / "worker.log").open("ab") as output:
            try:
                worker = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "mcp_manager.interfaces.source_worker",
                        str(backend.home),
                        identifier,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=output,
                    env={
                        **os.environ,
                        "PYTHONPATH": str(Path(__file__).resolve().parents[2])
                        + os.pathsep
                        + os.environ.get("PYTHONPATH", ""),
                    },
                    start_new_session=os.name == "posix",
                )
            except OSError as error:
                row.update(
                    status="failed",
                    error={"code": "io", "message": "Could not start source worker"},
                )
                atomic(path / "status.json", row)
                raise SourceError("io", "Could not start source worker") from error
        row["pid"] = worker.pid
        row["process_identity"] = process_identity(worker.pid)
        atomic(path / "status.json", row)
        threading.Thread(target=worker.wait, daemon=True).start()
        return row


def update(sources, job_id, **values):
    with sources.backend.lock():
        path = directory(sources, job_id) / "status.json"
        row = read(path)
        require(row is not None, "Source job not found", "not_found")
        if row["status"] == "cancelled":
            raise SourceError("cancelled", "Source job was cancelled")
        row.update(values, updated_at=time.time())
        atomic(path, row)
        return row


def guard(sources, job_id):
    # Call under the manager lock when committing resources or runtime manifests.
    row = read(directory(sources, job_id) / "status.json")
    require(
        row and row["status"] not in ("cancelled", "interrupted"),
        "Source job was cancelled",
        "cancelled",
    )


def cancel(sources, job_id):
    with sources.backend.lock():
        path = directory(sources, job_id) / "status.json"
        row = read(path)
        require(row is not None, "Source job not found", "not_found")
        if row["status"] not in ("queued", "running"):
            return row
        if (
            not row.get("process_identity")
            or process_identity(row["pid"]) != row["process_identity"]
        ):
            row.update(
                status="interrupted",
                updated_at=time.time(),
                error={
                    "code": "interrupted",
                    "message": "Worker identity is no longer available; no process was signalled",
                },
            )
            atomic(path, row)
            return row
        row.update(status="cancelled", updated_at=time.time())
        atomic(path, row)
    if row.get("pid"):
        try:
            if os.name == "posix":
                os.killpg(row["pid"], signal.SIGTERM)
            else:
                os.kill(row["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    return row


def run(sources, job_id):
    path = directory(sources, job_id)
    task = read(path / "request.json")
    operation, request = task["operation"], task["request"]
    try:
        update(sources, job_id, status="running", step=operation)
        options = dict(request)
        if operation == "import-github":
            selections = options.pop("selections", None)
            role = options.pop("role", "system")
            discovery = sources.discover(**options)
            result = sources.import_source(
                discovery["discovery_id"],
                selections,
                role=role,
                guard=lambda: guard(sources, job_id),
            )
        elif operation == "discover":
            result = sources.discover(**options)
        elif operation == "import":
            result = sources.import_source(
                **options, guard=lambda: guard(sources, job_id)
            )
        elif operation == "check":
            result = sources.check(**options, guard=lambda: guard(sources, job_id))
        elif operation == "switch-source":
            result = sources.switch(**options, guard=lambda: guard(sources, job_id))
        elif operation == "import-local":
            result = sources.import_local(
                **options, guard=lambda: guard(sources, job_id)
            )
        elif operation == "create":
            result = sources.create(**options, guard=lambda: guard(sources, job_id))
        elif operation == "install":
            from ..installations.service import install

            result = install(
                sources,
                task["capture"],
                path,
                guard=lambda: guard(sources, job_id),
                progress=lambda label: update(sources, job_id, step=label),
            )
        else:
            raise SourceError("validation", "Unknown source job operation")
        update(sources, job_id, status="completed", step="completed", result=result)
    except Exception as error:
        try:
            update(
                sources,
                job_id,
                status="failed",
                step="failed",
                error={
                    "code": getattr(error, "code", "io"),
                    "message": str(error)[:2048],
                },
            )
        except SourceError:
            pass
