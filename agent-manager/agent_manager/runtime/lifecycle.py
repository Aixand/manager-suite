"""Observe and retire managed runtime processes without touching native login."""
import asyncio
import inspect
import os
import shutil
from pathlib import Path
from agent_manager.storage.locks import locks
from agent_manager.profiles.identities import environment_id


def session_missing(error):
    """Use only for reads/watches of a known session, never launch retries."""
    import undercurrent
    return (isinstance(error, getattr(undercurrent, "SessionRemoved", ()))
            or getattr(error, "product_category", None) == "not_found"
            or getattr(error, "category", None) == "NotFound")


def observation_connection(record):
    """Replace a removed bridge executable without changing the saved daemon."""
    options = dict(record["connection"])
    bridge = options.get("bridge")
    if bridge and shutil.which(str(bridge)) is None:
        # A default home could now identify a different daemon. Without the
        # original home, observation cannot safely release this run's lease.
        if not options.get("home"):
            raise OSError("The original Undercurrent home is unknown")
        replacement = shutil.which(os.environ.get("UNDERCURRENT_PYTHON_BRIDGE", "uc-python-bridge"))
        if replacement:
            options["bridge"] = replacement
    return options


async def observe(record, *, runtime=None):
    """Consult the original runtime; an unavailable observer retains the lease."""
    try:
        import undercurrent
    except ImportError:
        return "unknown"
    try:
        async with asyncio.timeout(4):
            if runtime is None:
                observer = undercurrent.Client(**(observation_connection(record) | {"request_timeout": 3}))
                try:
                    await observer.connect()
                    session = await observer.inspect(record["session_id"])
                finally:
                    # __aexit__ would not run if the connection handshake failed.
                    await observer.close()
            else:
                # Callers may borrow their connected runtime only after matching
                # this run's saved connection. Do not close the borrowed client.
                session = await runtime.inspect(record["session_id"])
            return "finished" if session.lifecycle in ("stopped", "exited", "failed") else "running"
    except undercurrent.UndercurrentError as error:
        return "missing" if session_missing(error) else "unknown"
    except (TimeoutError, OSError):
        return "unknown"


def reconcile(manager, profile=None):
    with locks(manager.lock_root, "registry", wait=True):
        state = manager.state()
        records = {key: row for key, row in state.get("runs", {}).items()
                   if (profile is None or row.get("environment_id") == environment_id(state, profile))
                   and row.get("runtime") == "undercurrent" and row.get("session_id")}
    if not records:
        return

    async def check():
        return await asyncio.gather(*(observe(record) for record in records.values()))

    from agent_manager.runtime.runs import finish_run
    for (run_id, record), status in zip(records.items(), asyncio.run(check())):
        if status in ("finished", "missing"):
            finish_run(manager, run_id, status="finished", error="Runtime session no longer exists" if status == "missing" else None)

