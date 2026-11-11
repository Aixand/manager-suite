"""Managed run records, worker dispatch, and launch reconciliation."""
from __future__ import annotations
import asyncio
import json
import os
import subprocess
import sys
import threading
from agent_manager.errors import Error
from agent_manager.storage.files import atomic, private_dir
from agent_manager.storage.locks import locks
from agent_manager.validation import name
from agent_manager.runtime.context import attributes, from_record


from agent_manager.runtime.models import LaunchError, RunNotFoundError

def read_run(manager, run_id):
    name(run_id)
    with locks(manager.lock_root, "registry", wait=True):
        record = manager.state().get("runs", {}).get(run_id)
        from agent_manager.profiles.identities import resolve_record
        if record is not None: return resolve_record(manager.state(), record)
        path = manager.root / "sdk-runs" / (run_id + ".json")
        if path.exists():
            state = manager.state()
            return resolve_record(state, json.loads(path.read_text()))
    raise RunNotFoundError(f"Unknown managed run: {run_id}")


def update_run(manager, run_id, **changes):
    with locks(manager.lock_root, "registry", wait=True):
        state = manager.state()
        record = state.get("runs", {}).get(run_id)
        if record is None: return
        from agent_manager.profiles.identities import resolve_record
        record.update(resolve_record(state, record))
        record.update(changes)
        manager.save(state)


def keyed_run(manager, state, run_id, signature):
    """Compare caller intent against the recorded runtime under the registry lock.

    signature(record) preserves native-work checks while accepting transport-only
    changes for the same daemon. Finalized records are never replayed.
    """
    record = state.get("runs", {}).get(run_id)
    path = manager.root / "sdk-runs" / (run_id + ".json")
    if record is None and path.exists():
        record = json.loads(path.read_text())
    if record is not None and record.get("request_signature") != signature(record):
        raise Error("Idempotency key was reused with different launch arguments")
    return record


def finish_run(manager, run_id, *, status, error=None):
    """Finalize only after Undercurrent confirms termination or no launch effect."""
    with locks(manager.lock_root, "registry", wait=True):
        state = manager.state()
        record = state.get("runs", {}).get(run_id)
        if record is None: return
        from agent_manager.profiles.identities import resolve_record
        record.update(resolve_record(state, record))
        record.update(status=status, error=error)
        # Write history before removing the lease. Repeating after a crash is safe:
        atomic(manager.root / "sdk-runs" / (run_id + ".json"), json.dumps(record).encode())
        del state["runs"][run_id]
        manager.save(state)
    from agent_manager.runtime.session_leases import release
    release(manager, run_id)


def launch_worker(manager, run_id):
    private_dir(manager.root / "sdk-runs")
    log = manager.root / "sdk-runs" / (run_id + ".log")
    descriptor = os.open(log, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    args = [sys.executable, "-m", "agent_manager.runtime.supervisor", manager.provider,
            str(manager.root.parent.resolve()), run_id]
    if manager.config_path: args.extend(["--config", str(manager.config_path)])
    try:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=descriptor, start_new_session=True)
    finally:
        os.close(descriptor)
    # Reap our child without tying its lifetime to an asyncio event loop.
    threading.Thread(target=process.wait, daemon=True).start()


async def wait_for_launch(manager, run_id, timeout=60.0):
    try:
        async with asyncio.timeout(timeout):
            while True:
                record = await asyncio.to_thread(read_run, manager, run_id)
                if record.get("session_id"):
                    return record
                if record["status"] in ("failed", "uncertain"):
                    raise LaunchError(record.get("error") or "Managed launch failed", run_id)
                await asyncio.sleep(0.05)
    except TimeoutError:
        raise LaunchError("Timed out waiting for launch; the managed run was retained", run_id) from None


def reconcile_runs(manager):
    with locks(manager.lock_root, "registry", wait=True):
        runs = [key for key, row in manager.state().get("runs", {}).items()
                if row.get("runtime") == "undercurrent"]
    for run_id in runs:
        launch_worker(manager, run_id)
    return runs


def list_runs(manager, *, include_finished=True, limit=250, labels=None):
    """Return bounded public metadata, never credentials or launch environments.

    Active administrative leases precede retained history. Runtime observations
    remain in Undercurrent, including whether a finished session exited or stopped.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    selected = attributes(labels, "labels", internal=True)
    with locks(manager.lock_root, "registry", wait=True):
        records = {key: record for key, record in manager.state().get("runs", {}).items()
                   if record.get("runtime") == "undercurrent"}
        active_ids = set(records)
        if include_finished:
            directory = manager.root / "sdk-runs"
            paths = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
            for path in paths:
                if not selected and len(records) >= limit:
                    break
                if path.stem not in records:
                    try:
                        records[path.stem] = json.loads(path.read_text())
                    except (OSError, ValueError):
                        raise Error("Invalid managed run history") from None
        result = []
        for run_id, record in records.items():
            from agent_manager.profiles.identities import resolve_record
            record = resolve_record(manager.state(), record)
            if record.get("runtime") != "undercurrent":
                continue
            context = from_record(record)
            if not all(context["labels"].get(key) == value for key, value in selected.items()):
                continue
            status = record.get("status", "uncertain")
            result.append({
                "run_id": run_id, "provider": manager.provider,
                "name": record.get("launch", {}).get("name"),
                "profile": record["agent"],
                **{key:record.get(key) for key in ('profile_id', 'environment_id', 'home_id')},
                "session_id": record.get("session_id"), "status": status,
                "mode": record.get("mode", "terminal"),
                "thread_id": record.get("thread_id"),
                "launch_kind": record.get("launch_kind", "fresh"),
                "cwd": record.get("launch", {}).get("cwd"),
                "created_at_millis": record.get("created_at_millis"),
                "active": run_id in active_ids,
                "error": ("Launch needs reconciliation; it has not been replayed" if status == "uncertain"
                          else "Managed launch failed" if status == "failed" else None),
                **({"recovery": record["recovery"]} if record.get("recovery") else {}),
                "labels": context["labels"],
            })
        result.sort(key=lambda row: (not row["active"], -(row["created_at_millis"] or 0), row["run_id"]))
        return result[:limit]


