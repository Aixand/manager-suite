"""Standalone process owner. Managed launchers replace this implementation."""
from __future__ import annotations
from aixand_filesystem import private_directory, is_private
from aixand_local_runtime import process_start as native_process_start
import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import uuid

from .models import canonical, digest, receipt, ConversationError
from .store import Store
from .transport import LocalConnector


def process_start(pid):
    return native_process_start(pid)


def _socket_root():
    root = Path(tempfile.gettempdir())
    if sys.platform == "darwin":
        # _CS_DARWIN_USER_TEMP_DIR from unistd.h. The OS default is too long
        # for the retained worker address; preserve explicit custom temp roots.
        native_default = os.confstr(65537)
        if native_default and root.resolve() == Path(native_default).resolve():
            return Path("/tmp").resolve()
    return root


class StandaloneLauncher:
    def __init__(self, home=None, *, timeout=20, service_factory=None, service_options=None):
        self.store, self.timeout = Store(home), timeout
        self.service_factory, self.service_options = service_factory, service_options or {}

    async def launch(self, prepared, *, operation_id):
        if prepared.services is not None:
            raise ConversationError("unsupported", "Standalone worker cannot retain Python service objects; use worker service_factory or a supplied launcher")
        descriptor = prepared.to_dict()
        worker_id = uuid.uuid4().hex
        directory = _socket_root() / f"conversation-manager-{os.getuid() if hasattr(os, 'getuid') else os.environ.get('USERNAME', 'local')}" / digest(str(self.store.home))[7:23]
        endpoint = str(directory / (worker_id + ".sock"))
        # sockaddr_un has a byte limit, not a character limit. Check before any
        # worker/backend dispatch so a deterministic endpoint error is certain.
        maximum = 107 if sys.platform.startswith("linux") else 103
        if os.name != "nt" and len(os.fsencode(endpoint)) > maximum:
            raise ConversationError("validation", f"Worker Unix socket path exceeds {maximum} bytes; set TMPDIR to a shorter private root (for example /tmp)")
        if sys.platform == "darwin":
            private_directory(directory.parent)
            if not is_private(directory.parent.stat()):
                raise ConversationError("validation", "Worker socket parent must be private and owned by current user")
        private_directory(directory)
        if not is_private(directory.stat()):
            raise ConversationError("validation", "Worker socket directory must be private and owned by current user")
        # An asyncio subprocess transport is closed when a CLI's loop is collected,
        # potentially killing its child. Popen plus a daemon reaper has no loop owner.
        process = subprocess.Popen([sys.executable, "-I", "-B", "-m", "conversation_manager.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **({'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
               if os.name == 'nt' else {'start_new_session': True}))
        threading.Thread(target=process.wait, daemon=True).start()
        runtime = {"owner": "conversation-manager-standalone", "id": worker_id, "pid": process.pid,
                   "start_ticks": process_start(process.pid)}
        ref = dict(schema="conversation-manager/connection-ref-1", connection_id=prepared.connection_id,
            backend_id=prepared.backend_id, adapter_id=prepared.adapter_id, protocol=prepared.protocol,
            runtime_ref=runtime, transport={"kind": "conversation-manager-local", "endpoint": endpoint, "worker_id": worker_id})
        self.store.put("connection", ref["connection_id"], {"connection_ref": ref, "state": "starting"})
        self.store.finish(receipt(operation_id, "uncertain", recovery={"connection_ref": ref, "runtime_ref": runtime},
            error=ConversationError("uncertain", "Worker starting; initialization not yet observed", uncertain=True,
                              operation_id=operation_id, recovery={"connection_ref": ref}).to_dict()))
        try:
            bootstrap = canonical({"prepared": descriptor, "env": prepared.env,
                "home": str(self.store.home), "connection_ref": ref, "operation_id": operation_id,
                "service_factory": self.service_factory, "service_options": self.service_options}) + b"\n"
            def write_bootstrap():
                process.stdin.write(bootstrap)
                process.stdin.close()
            await asyncio.to_thread(write_bootstrap)
        except (OSError, ConnectionError) as exc:
            raise ConversationError("uncertain", "Worker bootstrap delivery is uncertain", uncertain=True,
                              operation_id=operation_id, recovery={"connection_ref": ref}) from exc
        deadline = asyncio.get_running_loop().time() + self.timeout
        while asyncio.get_running_loop().time() < deadline:
            row = self.store.operation(operation_id)
            if row["status"] in ("completed", "failed", "unsupported"):
                return dict(schema="conversation-manager/launch-receipt-1", operation_id=operation_id,
                    status=row["status"], runtime_ref=runtime, connection_ref=ref,
                    error=row["error"], recovery=row["recovery"])
            if process.poll() is not None:
                break
            await asyncio.sleep(.03)
        return dict(schema="conversation-manager/launch-receipt-1", operation_id=operation_id,
            status="uncertain", runtime_ref=runtime, connection_ref=ref,
            error=ConversationError("uncertain", "Worker readiness unconfirmed; do not launch a replacement",
                              uncertain=True, operation_id=operation_id).to_dict(), recovery={"connection_ref": ref})

    async def inspect(self, runtime_ref):
        if runtime_ref.get("owner") != "conversation-manager-standalone":
            raise ConversationError("unsupported", "Inspect or terminate this runtime through its recorded owner")
        row = next((r for r in self.store.all("connection") if r["connection_ref"]["runtime_ref"] == runtime_ref), None)
        if row is None:
            raise ConversationError("not_found", "Runtime identity is not owned by this launcher")
        live = runtime_ref.get("start_ticks") is not None and process_start(runtime_ref["pid"]) == runtime_ref["start_ticks"]
        return {"runtime_ref": runtime_ref, "state": "running" if live else "stopped"}

    async def terminate(self, runtime_ref, *, operation_id):
        intent = {"method": "terminate", "runtime_ref": runtime_ref}
        row, fresh = self.store.claim(operation_id, intent, {"runtime_ref": runtime_ref})
        if not fresh:
            return row
        try:
            observation = await self.inspect(runtime_ref)
        except ConversationError as exc:
            return self.store.finish(receipt(operation_id, "unsupported" if exc.code == "unsupported" else "failed",
                error=exc.to_dict(), recovery={"runtime_ref": runtime_ref}))
        if observation["state"] == "running":
            ref = next(r["connection_ref"] for r in self.store.all("connection") if r["connection_ref"]["runtime_ref"] == runtime_ref)
            try:
                handle = await LocalConnector(timeout=2).attach(ref)
                await handle.call("shutdown", {"operation_id": operation_id})
                await handle.close()
            except ConversationError:
                # Explicit process-owner termination only, with exact start identity checked.
                if process_start(runtime_ref["pid"]) == runtime_ref["start_ticks"]:
                    os.kill(runtime_ref["pid"], signal.SIGTERM) if os.name == "nt" else os.killpg(runtime_ref["pid"], signal.SIGTERM)
        deadline = asyncio.get_running_loop().time() + 8
        while process_start(runtime_ref["pid"]) == runtime_ref["start_ticks"] and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(.03)
        stopped = process_start(runtime_ref["pid"]) != runtime_ref["start_ticks"]
        return self.store.finish(receipt(operation_id, "completed" if stopped else "accepted",
            result={"runtime_ref": runtime_ref, "termination_requested": True, "stopped": stopped}))


def launcher_factory(options):
    return StandaloneLauncher(**options)
