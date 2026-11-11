"""Server-owned worker bootstrap over an injected process owner.

The owner starts/observes/stops processes. This module alone constructs worker
commands, connection records, bootstrap payloads and worker operation receipts.
"""

from __future__ import annotations
import asyncio
import inspect
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
from typing import Protocol

from aixand_local_runtime import start_local_server
from aixand_filesystem import private_path
from .models import canonical, receipt, operation_key, ConversationError
from .store import Store


class ProcessOwner(Protocol):
    async def start(
        self, command: list[str], *, cwd: str, operation_id: str
    ) -> dict: ...
    async def inspect(self, runtime_ref: dict) -> dict: ...
    async def stop(self, runtime_ref: dict, *, operation_id: str) -> None: ...


class WorkerLauncher:
    def __init__(
        self,
        owner: ProcessOwner,
        home=None,
        *,
        timeout=30,
        service_factory=None,
        service_options=None,
        on_connection=None,
    ):
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 < timeout <= 300
        ):
            raise ConversationError(
                "validation", "Worker timeout must be between 0 and 300 seconds"
            )
        self.owner, self.store, self.timeout = owner, Store(home), timeout
        self.service_factory, self.service_options = (
            service_factory,
            service_options or {},
        )
        self.on_connection = on_connection

    async def launch(self, prepared, *, operation_id):
        operation_key(operation_id)
        if prepared.services is not None:
            raise ConversationError(
                "unsupported", "Retained services require a worker service_factory"
            )
        descriptor = prepared.to_dict()
        if not prepared.connection_id:
            raise ConversationError(
                "validation",
                "Use Client.launch to allocate the worker connection before dispatch",
            )
        folder = Path(tempfile.mkdtemp(prefix="server-worker-"))
        os.chmod(folder, 0o700)
        bootstrap_path, endpoint = (
            str(folder / "bootstrap.sock"),
            str(folder / "worker.sock"),
        )
        if (
            os.name != "nt"
            and max(len(os.fsencode(path)) for path in (bootstrap_path, endpoint))
            >= 104
        ):
            shutil.rmtree(folder)
            raise ConversationError(
                "validation",
                "Temporary directory is too long for managed worker sockets",
            )
        ready = asyncio.get_running_loop().create_future()
        delivered = asyncio.Event()
        claimed = False

        async def bootstrap(reader, writer):
            nonlocal claimed
            try:
                if claimed:
                    return
                claimed = True
                payload = await asyncio.shield(ready)
                writer.write(canonical(payload) + b"\n")
                await writer.drain()
                delivered.set()
            finally:
                writer.close()
                await writer.wait_closed()

        server = None
        runtime_ref, ref = None, None
        try:
            server = await start_local_server(bootstrap, bootstrap_path)
            private_path(bootstrap_path)
            command = [
                sys.executable,
                "-I",
                "-B",
                "-m",
                "conversation_manager.worker",
                "--bootstrap-socket",
                bootstrap_path,
            ]

            async def checkpoint():
                nonlocal ref
                ref = dict(
                    schema="conversation-manager/connection-ref-1",
                    connection_id=prepared.connection_id,
                    backend_id=prepared.backend_id,
                    adapter_id=prepared.adapter_id,
                    protocol=prepared.protocol,
                    runtime_ref=runtime_ref,
                    transport=dict(
                        kind="conversation-manager-local",
                        endpoint=endpoint,
                        worker_id=uuid.uuid4().hex,
                    ),
                )
                prior = self.store.get("connection", prepared.connection_id) or {}
                self.store.put(
                    "connection",
                    prepared.connection_id,
                    {**prior, "connection_ref": ref, "state": "starting"},
                )
                self.store.finish(
                    receipt(
                        operation_id,
                        "uncertain",
                        recovery={"runtime_ref": runtime_ref, "connection_ref": ref},
                        error=ConversationError(
                            "uncertain",
                            "Worker starting; initialization not yet observed",
                            uncertain=True,
                        ).to_dict(),
                    )
                )
                if self.on_connection is not None:
                    result = self.on_connection(ref)
                    if inspect.isawaitable(result):
                        await result

            provisional = getattr(self.owner, "prepare", None)
            if provisional is not None:
                runtime_ref = provisional(
                    command, cwd=prepared.cwd, operation_id=operation_id
                )
                await checkpoint()
                ready.set_result(
                    dict(
                        prepared=descriptor,
                        env=prepared.env,
                        home=str(self.store.home),
                        connection_ref=ref,
                        operation_id=operation_id,
                        service_factory=self.service_factory,
                        service_options=self.service_options,
                    )
                )
            observed_runtime = await self.owner.start(
                command, cwd=prepared.cwd, operation_id=operation_id
            )
            if (
                not isinstance(observed_runtime, dict)
                or not observed_runtime.get("owner")
                or not observed_runtime.get("id")
            ):
                raise ConversationError(
                    "uncertain",
                    "Process owner returned an invalid runtime identity",
                    uncertain=True,
                )
            if runtime_ref is not None and observed_runtime != runtime_ref:
                raise ConversationError(
                    "uncertain",
                    "Process owner changed its reserved runtime identity",
                    uncertain=True,
                )
            runtime_ref = observed_runtime
            if ref is None:
                await checkpoint()
            if not ready.done():
                ready.set_result(
                    dict(
                        prepared=descriptor,
                        env=prepared.env,
                        home=str(self.store.home),
                        connection_ref=ref,
                        operation_id=operation_id,
                        service_factory=self.service_factory,
                        service_options=self.service_options,
                    )
                )
            await asyncio.wait_for(delivered.wait(), timeout=self.timeout)
            deadline = asyncio.get_running_loop().time() + self.timeout
            while asyncio.get_running_loop().time() < deadline:
                row = self.store.operation(operation_id)
                if row["status"] in ("completed", "failed", "unsupported"):
                    return dict(
                        schema="conversation-manager/launch-receipt-1",
                        operation_id=operation_id,
                        status=row["status"],
                        runtime_ref=runtime_ref,
                        connection_ref=ref,
                        error=row["error"],
                        recovery=row["recovery"],
                    )
                observed = await self.owner.inspect(runtime_ref)
                if observed.get("runtime_ref") != runtime_ref:
                    raise ConversationError(
                        "uncertain",
                        "Process owner observed a different runtime",
                        uncertain=True,
                    )
                if observed["state"] in ("stopped", "exited", "failed"):
                    break
                await asyncio.sleep(0.03)
            raise ConversationError(
                "uncertain",
                "Worker readiness is unconfirmed; inspect without replacement",
                uncertain=True,
            )
        except Exception as error:
            if runtime_ref is not None:
                observed = self.store.operation(operation_id)
                if (
                    ref is not None
                    and observed["status"] in ("completed", "failed", "unsupported")
                    and observed.get("recovery", {}).get("connection_ref") == ref
                ):
                    return dict(
                        schema="conversation-manager/launch-receipt-1",
                        operation_id=operation_id,
                        status=observed["status"],
                        runtime_ref=runtime_ref,
                        connection_ref=ref,
                        error=observed["error"],
                        recovery=observed["recovery"],
                    )
                recovery = {
                    **getattr(error, "recovery", {}),
                    "runtime_ref": runtime_ref,
                    "connection_ref": ref,
                }
                raise ConversationError(
                    "uncertain",
                    "Worker startup outcome is unconfirmed; inspect without replacement",
                    uncertain=True,
                    operation_id=operation_id,
                    recovery=recovery,
                ) from error
            raise
        finally:
            if not ready.done():
                ready.cancel()
            if server is not None:
                server.close()
                await server.wait_closed()
            Path(bootstrap_path).unlink(missing_ok=True)
            # Once dispatch was attempted, the worker may still exist even if
            # the owner could not return its identity. Preserve its socket paths.
            if runtime_ref is None and not claimed:
                try:
                    folder.rmdir()
                except OSError:
                    pass

    async def inspect(self, runtime_ref):
        return await self.owner.inspect(runtime_ref)

    async def terminate(self, runtime_ref, *, operation_id):
        operation_key(operation_id)
        observation = await self.owner.inspect(runtime_ref)
        if observation.get("runtime_ref") != runtime_ref:
            raise ConversationError(
                "conflict", "Process owner observed a different runtime"
            )
        row, fresh = self.store.claim(
            operation_id,
            {"method": "terminate", "runtime_ref": runtime_ref},
            {"runtime_ref": runtime_ref},
        )
        if observation["state"] in ("stopped", "exited", "failed"):
            return self.store.finish(
                receipt(
                    operation_id,
                    "completed",
                    result={
                        "runtime_ref": runtime_ref,
                        "termination_requested": True,
                        "stopped": True,
                    },
                    recovery={"runtime_ref": runtime_ref},
                )
            )
        if not fresh:
            return row
        try:
            await self.owner.stop(runtime_ref, operation_id=operation_id)
            return self.store.finish(
                receipt(
                    operation_id,
                    "accepted",
                    result={"runtime_ref": runtime_ref, "termination_requested": True},
                    recovery={"runtime_ref": runtime_ref},
                )
            )
        except Exception:
            return self.store.finish(
                receipt(
                    operation_id,
                    "uncertain",
                    recovery={"runtime_ref": runtime_ref},
                    error=ConversationError(
                        "uncertain",
                        "Termination delivery is unconfirmed; inspect the recorded runtime",
                        uncertain=True,
                        operation_id=operation_id,
                        recovery={"runtime_ref": runtime_ref},
                    ).to_dict(),
                )
            )
