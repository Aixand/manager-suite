"""Own a fresh native server and attach its terminal to an exact source rollout.

Executed inside the process-local writer-lock mount namespace. The server uses
the destination home for login/configuration and the source native SQLite store.
No history is copied, forked or reconstructed through the model API.
"""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import traceback

from agent_manager._version import __version__
from agent_manager.errors import Error
from agent_manager.runtime.native.profile_environment import environment


def native_command(launch, endpoint):
    """One native command for socket launches and isolated stdio verification."""
    return [launch["program"], "app-server", "--listen", endpoint,
        *launch["server_args"], "-c", "sqlite_home=" + json.dumps(launch["history_home"], ensure_ascii=False)]


def allowed(message, launch):
    """One terminal stays with the original thread; other profiles own new work."""
    method = message.get("method", "")
    params = message.get("params") or {}
    if "threadId" in params and params["threadId"] != launch["thread_id"]:
        return False
    if method in ("thread/start", "thread/fork", "thread/archive", "thread/unarchive", "thread/delete", "thread/close", "thread/unload"):
        return False
    if method == "thread/resume":
        path = params.get("path")
        cwd = params.get("cwd")
        return (params.get("threadId") == launch["thread_id"] and params.get("history") is None
            and (not path or Path(path).resolve() == Path(launch["rollout"]).resolve())
            and (not cwd or Path(cwd).resolve() == Path(launch["cwd"]).resolve()))
    return True


def shared_writer(launch):
    """Verify both path identity and an actual native writer before any turn."""
    import fcntl
    with Path(launch["coordination"]).open("rb") as coordination:
        fcntl.flock(coordination, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with Path(launch["source_lock"]).open("rb") as stream:
            source = os.fstat(stream.fileno())
            mounted = Path(launch["target_lock"]).stat()
            if (source.st_dev, source.st_ino) != (mounted.st_dev, mounted.st_ino):
                raise Error("The original native writer-lock inode changed during continuation")
            try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return
            raise Error("Codex did not acquire the original conversation writer lock")


async def verify_writer(launch, *, timeout=2):
    """Wait only for transient coordination contention, never replay a request."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            shared_writer(launch)
            return
        except BlockingIOError:
            if asyncio.get_running_loop().time() >= deadline:
                raise
            await asyncio.sleep(.05)


@contextlib.asynccontextmanager
async def terminal_endpoint(upstream, endpoint, launch):
    """Forward native RPC IDs/approvals, rejecting conversation replacement."""
    from aixand_local_runtime.websocket import connect, serve
    from websockets.exceptions import ConnectionClosed, ConnectionClosedError

    def disconnected(error):
        # Close reasons may contain native payloads. Retain only close codes;
        # WebSocket handler tracebacks otherwise overwrite the native TUI.
        if not isinstance(error, ConnectionClosedError) or not launch.get("diagnostics"):
            return
        received = error.rcvd.code if error.rcvd else "none"
        sent = error.sent.code if error.sent else "none"
        try:
            with Path(launch["diagnostics"]).open("a") as log:
                print(f"Native terminal connection closed (received={received}, sent={sent}); requests were not replayed.", file=log)
        except OSError:
            pass

    # Native history replies can exceed 16 MiB. This private local proxy must
    # preserve native message sizes rather than forcing a reconnect loop.
    async def forward(downstream):
        async with connect(upstream, compression=None, max_size=None) as native:
            async def receive():
                try:
                    async for raw in native: await downstream.send(raw)
                except ConnectionClosed as error:
                    disconnected(error)
                finally: await downstream.close()
            receiver = asyncio.create_task(receive())
            try:
                async for raw in downstream:
                    message = json.loads(raw)
                    permitted = allowed(message, launch)
                    rejection = "conversation-identity" if not permitted else None
                    if permitted and message.get("method") in ("thread/resume", "turn/start", "thread/goal/set"):
                        try: await verify_writer(launch)
                        except (Error, OSError):
                            permitted = False
                            rejection = "native-writer-evidence"
                    if not permitted:
                        diagnostic = launch.get("diagnostics")
                        if diagnostic:
                            method = message.get("method", "")
                            safe_method = method if isinstance(method, str) and len(method) <= 64 and all(c.isalnum() or c in "/_-" for c in method) else "unknown"
                            try:
                                with Path(diagnostic).open("a") as log:
                                    print("Rejected native request: " + safe_method + " (" + rejection + ")", file=log)
                            except OSError:
                                pass  # A diagnostics failure must not replace the refusal reply.
                        if "id" in message:
                            await downstream.send(json.dumps({"id": message["id"], "error": {
                                "code": -32078, "message": "This continuation is bound to its original conversation. Start another agent launch for new work."}}))
                        continue
                    if message.get("method") == "thread/list":
                        # The source store is already indexed. Never scan the
                        # execution profile's unrelated local session directory.
                        message["params"] = {**(message.get("params") or {}), "useStateDbOnly": True}
                        raw = json.dumps(message)
                    await native.send(raw)
            finally:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)
    async def peer(downstream):
        try:
            await forward(downstream)
        except ConnectionClosed as error:
            disconnected(error)

    server = await serve(peer, endpoint, compression=None, max_size=None)
    os.chmod(endpoint, 0o600)
    try: yield
    finally:
        server.close()
        await server.wait_closed()


async def run(descriptor):
    from agent_manager.runtime.native.codex_wire import NativeCodexConnection
    path = Path(descriptor)
    launch = json.loads(path.read_text(encoding="utf-8"))
    launch["diagnostics"] = str(path.with_name("native-server.log"))
    home = Path(launch["home"]).resolve()
    history_home = Path(launch["history_home"]).resolve()
    if Path(os.environ.get("CODEX_HOME", "")).resolve() != home:
        raise Error("Continuation execution home changed before native launch")
    variables = environment("codex", os.environ)
    variables.update(CODEX_HOME=str(home), CODEX_SQLITE_HOME=str(history_home))
    # Keep diagnostics provider-owned and private; never echo native raw output.
    log = path.with_name("native-server.log")
    fd = os.open(log, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    server = None
    with os.fdopen(fd, "ab", buffering=0) as diagnostics, tempfile.TemporaryDirectory(prefix="agent-codex-rpc-") as folder, contextlib.ExitStack() as stack:
        socket = str(Path(folder) / "server.sock")
        terminal = str(Path(folder) / "terminal.sock")
        if max(len(os.fsencode(socket)), len(os.fsencode(terminal))) >= 104:
            raise Error("Temporary directory is too long for a native Codex Unix socket")
        try:
            import fcntl
            coordination = stack.enter_context(Path(launch["coordination"]).open("rb"))
            fcntl.flock(coordination, fcntl.LOCK_EX | fcntl.LOCK_NB)
            source = Path(launch["source_lock"]).stat()
            mounted = Path(launch["target_lock"]).stat()
            if (source.st_dev, source.st_ino) != (mounted.st_dev, mounted.st_ino):
                raise Error("The original native writer-lock inode changed during continuation")
            # Native resume performs its own coordination. Check the shared
            # inode again after its response and before allowing a user turn.
            fcntl.flock(coordination, fcntl.LOCK_UN)
            server = subprocess.Popen(native_command(launch, "unix://" + socket),
                stdin=subprocess.DEVNULL, stdout=diagnostics, stderr=diagnostics,
                env=variables, cwd=launch["cwd"])
            async with NativeCodexConnection(socket, timeout=30,
                    capabilities={"experimentalApi": True},
                    client_info={"name": "agent_manager", "title": "Agent Manager", "version": __version__}) as wire:
                effective = await wire.request("config/read", {"cwd": launch["cwd"], "includeLayers": False})
                location = effective.get("config", {}).get("sqlite_home")
                if not isinstance(location, str) or Path(location).resolve() != history_home:
                    raise Error("Codex did not select the original native SQLite history store")
                result = await wire.request("thread/resume", {"threadId": launch["thread_id"],
                    "path": launch["rollout"], "cwd": launch["cwd"], "excludeTurns": True,
                    **({"model": launch["model"]} if launch["model"] is not None else {})})
                thread = result.get("thread", {})
                if (thread.get("id") != launch["thread_id"] or not thread.get("path")
                        or Path(thread["path"]).resolve() != Path(launch["rollout"]).resolve()
                        or Path(thread.get("cwd", "")).resolve() != Path(launch["cwd"]).resolve()):
                    raise Error("Codex did not confirm the original conversation identity and history path")
                await verify_writer(launch)
                # The native writer now holds the shared inode. Cleanup in the
                # original home cannot replace it while the thread is active.
                # Keep the initialized connection alive until the TUI exits.
                # No source daemon is reused: this server owns the target login.
                async with terminal_endpoint(socket, terminal, launch):
                    tui = await asyncio.create_subprocess_exec(
                        launch["program"], "resume", launch["thread_id"], "--remote", "unix://" + terminal,
                        cwd=launch["cwd"], env=variables)
                    try:
                        return await tui.wait()
                    finally:
                        if tui.returncode is None:
                            tui.terminate()
                            try: await asyncio.wait_for(tui.wait(), timeout=5)
                            except asyncio.TimeoutError:
                                tui.kill()
                                await tui.wait()
        finally:
            if server is not None and server.poll() is None:
                server.terminate()
                try: await asyncio.to_thread(server.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    await asyncio.to_thread(server.wait)


async def supervise(descriptor):
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        return await run(descriptor)
    except asyncio.CancelledError:
        return 143
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


def main():
    try:
        return asyncio.run(supervise(sys.argv[1]))
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        diagnostics = Path(sys.argv[1]).with_name("native-server.log")
        try:
            fd = os.open(diagnostics, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as stream:
                stream.write("\nAgent Manager continuation failure:\n")
                traceback.print_exception(error, file=stream)
        except OSError:
            print("The continuation failure could not be written to its diagnostic log.", file=sys.stderr)
        print("Codex continuation could not be confirmed. No replacement conversation was created. "
              f"Inspect {diagnostics} before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
