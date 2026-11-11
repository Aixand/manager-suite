"""Retained worker entry point, usable by a supplied process supervisor.

One finite JSON bootstrap line on stdin: prepared (portable), env (transient),
home, connection_ref, operation_id; optional service_factory MODULE:factory and
service_options. Never write bootstrap credentials to disk.
"""

from __future__ import annotations

from aixand_local_runtime import start_local_server
import argparse
import asyncio
import importlib
import os
from aixand_local_runtime import install_signals
import signal
from aixand_local_runtime import connect_local
import sys
from pathlib import Path

from .core import Core
from .models import PreparedLaunch, ConversationError, canonical, decode, receipt
from .store import Store
from .transport import IPC_SCHEMA, LIMIT


async def serve(bootstrap):
    os.umask(0o077)
    store = Store(bootstrap["home"])
    prepared = PreparedLaunch.from_dict(
        bootstrap["prepared"], env=bootstrap.get("env", {})
    )
    if (prepared.protocol == "claude-agent-sdk"
            and any(ref.get("provider") == "claude" and ref.get("method") == "profile-login" for ref in prepared.credential_refs)
            and prepared.env.get("CLAUDE_CONFIG_DIR") == ""):
        # This dedicated worker owns one launch. The SDK merges os.environ and
        # cannot unset a variable per call; native Claude requires this variable
        # to be absent, rather than empty, to use its sibling .claude.json.
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
    ref, operation_id = bootstrap["connection_ref"], bootstrap["operation_id"]
    services = None
    if bootstrap.get("service_factory"):
        module, name = bootstrap["service_factory"].split(":", 1)
        services = getattr(importlib.import_module(module), name)(
            bootstrap.get("service_options", {})
        )
    core = Core(store, prepared, ref, services=services)
    endpoint = Path(ref["transport"]["endpoint"])
    server = None
    try:
        async with asyncio.timeout(15):
            await core.adapter.start()
        store.put(
            "connection",
            ref["connection_id"],
            {"connection_ref": ref, "state": "ready", "epoch": core.epoch},
        )

        async def handle(reader, writer):
            try:
                async with asyncio.timeout(10):
                    envelope = decode(await reader.readline())
                if (
                    envelope.get("schema") != IPC_SCHEMA
                    or envelope.get("connection_ref") != ref
                ):
                    raise ConversationError(
                        "conflict",
                        "IPC attachment does not identify this exact retained worker/runtime",
                    )
                # Frontend EOF or waiter cancellation must never cancel retained dispatch.
                task = core.spawn(
                    core.dispatch(envelope["method"], envelope.get("params", {}))
                )
                result = await asyncio.shield(task)
                response = {"schema": IPC_SCHEMA, "result": result}
            except ConversationError as exc:
                response = {"schema": IPC_SCHEMA, "error": exc.to_dict()}
            except Exception:
                response = {
                    "schema": IPC_SCHEMA,
                    "error": ConversationError(
                        "validation", "Invalid worker request"
                    ).to_dict(),
                }
            try:
                writer.write(canonical(response) + b"\n")
                await writer.drain()
            except (OSError, ConnectionError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, ConnectionError):
                    pass

        server = await start_local_server(handle, str(endpoint), limit=LIMIT)
        os.chmod(endpoint, 0o600)
        store.finish(
            receipt(
                operation_id,
                "completed",
                result={"connection_ref": ref},
                recovery={"connection_ref": ref, "runtime_ref": ref["runtime_ref"]},
            )
        )
        loop = asyncio.get_running_loop()
        install_signals(core.shutdown.set)
        await core.shutdown.wait()
    except Exception as exc:
        core.finish_error(operation_id, core.error(exc, operation_id, dispatched=True))
    finally:
        core.transport_lost("Worker stopped")
        if server:
            server.close()
            await server.wait_closed()
        await core.adapter.close()
        for task in list(core.tasks):
            task.cancel()
        await asyncio.gather(*core.tasks, return_exceptions=True)
        endpoint.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bootstrap-socket",
        help="Private one-shot bootstrap socket supplied by a managed launcher",
    )
    args = parser.parse_args()
    if args.bootstrap_socket:
        # Supervisors may own a PTY rather than a pipe. Transient secrets must
        # never be submitted as terminal input or retained in terminal output.
        with connect_local(args.bootstrap_socket, timeout=30) as channel:
            with channel.makefile("rb") as stream:
                bootstrap = decode(stream.readline(LIMIT))
    else:
        bootstrap = decode(sys.stdin.buffer.readline(LIMIT))
    asyncio.run(serve(bootstrap))


if __name__ == "__main__":
    main()
