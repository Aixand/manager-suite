"""Native WebSocket forwarding with process-wide admission and drain inspection.

The upstream process is owned by the supplied runtime, never by this worker.
Each downstream owns a separate upstream connection, preserving native RPC IDs
and approval ownership. The control endpoint carries metadata, not credentials.
"""
from __future__ import annotations

from aixand_local_runtime import start_local_server, open_local_connection
import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
from aixand_local_runtime import install_signals
import signal
import tempfile
import uuid

from .native_codex import NativeCodexConnection, MAX_MESSAGE_BYTES


SCHEMA = "conversation-manager/native-controller-1"
DRAIN_ERROR = -32079
# During draining, unknown methods fail before dispatch. Ordinary forwarding is
# transparent, including provider extensions. Responses to native requests pass.
DRAIN_READS = frozenset({
    "initialize", "initialized", "thread/read", "thread/loaded/list",
    "thread/turns/list", "thread/items/list", "thread/list", "thread/goal/get",
    "thread/queue/list", "thread/backgroundTerminals/list", "account/read",
    "account/rateLimits/read", "config/read", "model/list", "server/diagnostics",
    "permissionProfile/list", "collaborationMode/list", "turn/interrupt",
    "thread/realtime/stop", "process/kill", "command/exec/terminate",
    "thread/backgroundTerminals/terminate", "thread/queue/delete", "thread/goal/clear",
    "account/login/cancel",
})
READS = DRAIN_READS - {"turn/interrupt", "thread/realtime/stop", "process/kill", "command/exec/terminate",
                      "thread/backgroundTerminals/terminate", "thread/queue/delete", "thread/goal/clear", "account/login/cancel"}


def drain_safe(method, params=None):
    params = params or {}
    return method in DRAIN_READS or (method == "thread/goal/set" and params.get("status") == "paused"
        and set(params) <= {"threadId", "status"})


def rpc_key(peer, identifier):
    # JSON-RPC numeric IDs and string IDs inhabit different namespaces.
    return peer + ":" + json.dumps(identifier, separators=(",", ":"))


def write_state(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, separators=(",", ":"), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


async def control(endpoint, epoch, method="status", *, operation_id=None, timeout=10):
    """Attach to this exact controller. A timeout never implies it is idle."""
    async with asyncio.timeout(timeout):
        reader, writer = await open_local_connection(str(endpoint), limit=1_048_576)
        try:
            writer.write((json.dumps({"schema": SCHEMA, "epoch": epoch, "method": method,
                                      "operation_id": operation_id}) + "\n").encode())
            await writer.drain()
            reply = json.loads(await reader.readline())
            if reply.get("schema") != SCHEMA or reply.get("epoch") != epoch or "error" in reply:
                raise ValueError("Native controller identity or operation did not match")
            result = reply['result']
            if method in ('begin_drain', 'prepare_stop') and result.get('activity_version') != 2:
                result = {**result, 'ready': False, 'blockers': [*result.get('blockers', []),
                          {'kind': 'login_activity_unavailable'}]}
            return result
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()


class NativeGateway:
    def __init__(self, upstream, socket, endpoint, epoch, *, timeout=5, managed_auth=False):
        self.upstream, self.socket, self.endpoint = map(Path, (upstream, socket, endpoint))
        self.epoch, self.timeout = epoch, timeout
        self.managed_auth = managed_auth
        self.path = self.endpoint.with_suffix(".json")
        self.state = {"epoch": epoch, 'activity_version': 2, 'login_tracking_unavailable': False,
                      "draining": None, "inflight": {}, "requests": {},
                      "realtime": [], "processes": [], "logins": {}, "login_outcomes": {},
                      "uncertain": False, "sealed": False}
        if self.path.exists():
            previous = json.loads(self.path.read_text())
            if previous.get("epoch") != epoch:
                raise ValueError("Native controller epoch changed")
            if previous.get('activity_version') not in (None, 2):
                raise ValueError('Native controller activity version is unsupported')
            self.state.update(previous)
            if previous.get('activity_version') is None:
                self.state['login_tracking_unavailable'] = True
            if any(previous.get(key) for key in ("inflight", "requests", "realtime", "processes")):
                self.state["uncertain"] = True
        self.gate = asyncio.Lock()
        self.peers, self.tasks = {}, set()
        self.server = self.admin = self.observer = None

    def save(self):
        write_state(self.path, self.state)

    async def start(self):
        from aixand_local_runtime.websocket import serve as unix_serve
        # Publish the public endpoint only after the native listener is ready.
        self.observer = NativeCodexConnection(str(self.upstream), timeout=15,
            capabilities={"experimentalApi": True}, on_event=self.observer_event)
        await self.observer.connect()
        self.save()
        self.server = await unix_serve(self.peer, str(self.socket), compression=None,
            max_size=MAX_MESSAGE_BYTES, max_queue=4)
        os.chmod(self.socket, 0o600)
        self.admin = await start_local_server(self.admin_peer, str(self.endpoint), limit=1_048_576)
        os.chmod(self.endpoint, 0o600)
        return self

    async def close(self):
        if self.admin:
            self.admin.close()
            await self.admin.wait_closed()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.observer:
            await self.observer.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.socket.unlink(missing_ok=True)
        self.endpoint.unlink(missing_ok=True)

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def peer(self, downstream):
        from aixand_local_runtime.websocket import connect as unix_connect
        peer = uuid.uuid4().hex
        self.peers[peer] = {"managed": False}
        upstream = None
        try:
            upstream = await unix_connect(str(self.upstream), compression=None,
                max_size=MAX_MESSAGE_BYTES, max_queue=4, open_timeout=self.timeout)

            async def receive():
                try:
                    async for raw in upstream:
                        msg = json.loads(raw)
                        self.observe(peer, msg)
                        await downstream.send(raw)
                finally:
                    await downstream.close()
            receiver = self.spawn(receive())
            try:
                async for raw in downstream:
                    msg = json.loads(raw)
                    method = msg.get("method")
                    key = rpc_key(peer, msg.get("id"))
                    async with self.gate:
                        rejoin = False
                        if (self.state["draining"] and not self.state["sealed"] and method == "thread/resume"
                                and set(msg.get("params") or {}) == {"threadId"}):
                            try:
                                thread = (await self.observer.request("thread/read", {
                                    "threadId": msg["params"]["threadId"], "includeTurns": False}))["thread"]
                                status = thread.get("status")
                                rejoin = (status.get("type") if isinstance(status, dict) else status) in ("active", "idle")
                            except Exception:
                                pass
                        allowed = method in READS if self.state["sealed"] else drain_safe(method, msg.get("params"))
                        if self.managed_auth and (method == 'account/logout' or (
                                method and method.startswith('account/login/') and method != 'account/login/cancel')):
                            if 'id' in msg:
                                await downstream.send(json.dumps({'id': msg['id'], 'error': {
                                    'code': -32078, 'message': 'Use Agent Manager sign-in for this managed home (agent codex login --auth NAME, or SDK sign_in).',
                                    'data': {'code': 'managed_login_required', 'dispatched': False}}}))
                            continue
                        if self.state["draining"] and method and not allowed and not rejoin:
                            if "id" in msg:
                                await downstream.send(json.dumps({"id": msg["id"], "error": {
                                    "code": DRAIN_ERROR, "message": "This server is reconnecting after a login change; retry when recovery finishes",
                                    "data": {"code": "auth_recovery_pending", "dispatched": False,
                                             "operation_id": self.state["draining"]}}}))
                            continue
                        if method == "initialize":
                            info = (msg.get("params") or {}).get("clientInfo") or {}
                            self.peers[peer]["managed"] = info.get("name") == "conversation_manager"
                        if method and method not in READS and "id" in msg:
                            params = msg.get("params") or {}
                            self.state["inflight"][key] = {"method": method, "threadId": params.get("threadId"),
                                                         "processHandle": params.get("processHandle"),
                                                         **({'loginId': params.get('loginId')} if method == 'account/login/cancel' else {})}
                            if method == 'account/login/start':
                                self.state['logins'][key] = {'phase': 'starting', 'loginId': None}
                            if method == "process/spawn":
                                self.state["processes"].append(peer + ":" + str(params.get("processHandle")))
                            if method == "thread/realtime/start" and isinstance(params.get("threadId"), str):
                                self.state["realtime"] = sorted(set(self.state["realtime"]) | {params.get("threadId")})
                            self.save()
                        elif method and method not in READS and "id" not in msg:
                            # Unknown notification effects have no response with
                            # which to settle their dispatch.
                            self.state["uncertain"] = True
                            self.save()
                        # A forwarded approval response is not confirmation of
                        # resolution. Keep its activity until a native resolved
                        # notification or completed turn establishes the effect.
                        # Admission and registration precede the send, under one
                        # gate. Lost delivery remains uncertain; it is not replayed.
                        await upstream.send(raw)
            finally:
                await upstream.close()
                await asyncio.gather(receiver, return_exceptions=True)
        except Exception:
            pass
        finally:
            if any(key.startswith(peer + ":") for key in self.state["inflight"]):
                self.state["uncertain"] = True
                self.save()
            self.peers.pop(peer, None)
            if upstream:
                await upstream.close()
            await downstream.close()

    def observe(self, peer, msg):
        key = rpc_key(peer, msg.get("id"))
        method, params = msg.get("method"), msg.get("params") or {}
        changed = True
        if method and "id" in msg:
            self.state["requests"][key] = {"threadId": params.get("threadId")}
        elif "id" in msg:
            row = self.state["inflight"].pop(key, None)
            changed = row is not None
            if row and row["method"] == "process/spawn" and "error" in msg:
                handle = peer + ":" + str(row["processHandle"])
                self.state["processes"] = [v for v in self.state["processes"] if v != handle]
            if row and row["method"] == "thread/realtime/stop" and "error" not in msg:
                self.state["realtime"] = [v for v in self.state["realtime"] if v != row["threadId"]]
            if row and row["method"] == "thread/realtime/start" and "error" in msg:
                self.state["realtime"] = [v for v in self.state["realtime"] if v != row["threadId"]]
            if row and row['method'] == 'account/login/start':
                result = msg.get('result') or {}
                login_id = result.get('loginId')
                if 'error' in msg or result.get('type') in ('apiKey', 'chatgptAuthTokens'):
                    self.state['logins'].pop(key, None)
                elif isinstance(login_id, str) and login_id:
                    if login_id in self.state['login_outcomes']:
                        self.state['logins'].pop(key, None)
                        self.state['login_outcomes'].pop(login_id, None)
                    else:
                        self.state['logins'][key] = {'phase': 'awaiting-user', 'loginId': login_id}
                else:
                    self.state['uncertain'] = True
            if row and row['method'] == 'account/login/cancel' and 'error' not in msg and (msg.get('result') or {}).get('status') == 'canceled':
                self.settle_login(row.get('loginId'))
        elif method == 'account/login/completed':
            identifier = params.get('loginId')
            if isinstance(identifier, str) and isinstance(params.get('success'), bool):
                if not self.settle_login(identifier) and any(value['phase'] == 'starting' for value in self.state['logins'].values()):
                    # Completion can arrive before the start reply. Preserve
                    # only its identity/outcome, never the provider payload.
                    self.state['login_outcomes'][identifier] = params['success']
                    if len(self.state['login_outcomes']) > 256:
                        self.state['login_outcomes'].pop(next(iter(self.state['login_outcomes'])))
        elif method == "serverRequest/resolved":
            self.state["requests"].pop(rpc_key(peer, params.get("requestId")), None)
        elif method == "turn/completed":
            self.state["requests"] = {k: v for k, v in self.state["requests"].items()
                                      if v["threadId"] != params.get("threadId")}
        elif method in ("thread/realtime/closed", "thread/realtime/stopped"):
            self.state["realtime"] = [v for v in self.state["realtime"] if v != params.get("threadId")]
        elif method == "process/exited":
            handle = peer + ":" + str(params.get("processHandle"))
            self.state["processes"] = [v for v in self.state["processes"] if v != handle]
        else:
            changed = False
        # State stores identities and activity only, never RPC content/output.
        if changed:
            self.save()

    def settle_login(self, identifier):
        matched = [key for key, value in self.state['logins'].items() if identifier is not None and value.get('loginId') == identifier]
        for key in matched:
            self.state['logins'].pop(key)
        return bool(matched)

    def observer_event(self, message):
        if message.get('method') == 'account/login/completed':
            self.observe('observer', message)

    async def pages(self, method, params=None):
        values, cursor, seen = [], None, set()
        while True:
            page = await self.observer.request(method, {**(params or {}), **({"cursor": cursor} if cursor else {})})
            if not isinstance(page.get("data"), list):
                raise ValueError("Native activity enumeration unavailable")
            values.extend(page["data"])
            cursor = page.get("nextCursor")
            if not cursor:
                return values
            if cursor in seen or len(seen) > 100 or len(values) > 10000:
                raise ValueError("Native activity enumeration incomplete")
            seen.add(cursor)

    async def status(self):
        blockers = []
        for kind in ("inflight", "requests", "realtime", "processes", "logins"):
            if self.state[kind]:
                blockers.append({"kind": kind, "count": len(self.state[kind])})
        if self.state["uncertain"]:
            blockers.append({"kind": "uncertain_delivery"})
        if self.state['login_tracking_unavailable']:
            blockers.append({'kind': 'login_activity_unavailable'})
        attachments = sum(not value["managed"] for value in self.peers.values())
        if attachments:
            blockers.append({"kind": "native_clients", "count": attachments})
        try:
            if self.observer is None or not self.observer.connected:
                if self.observer:
                    await self.observer.close()
                self.observer = NativeCodexConnection(str(self.upstream), timeout=self.timeout,
                    capabilities={"experimentalApi": True}, on_event=self.observer_event)
                await self.observer.connect()
            remote = await self.observer.request("remoteControl/status/read", {})
            if remote.get("status") != "disabled":
                blockers.append({"kind": "remote_control"})
            for thread_id in await self.pages("thread/loaded/list"):
                thread = (await self.observer.request("thread/read", {"threadId": thread_id, "includeTurns": False}))["thread"]
                status = thread.get("status")
                kind = status.get("type") if isinstance(status, dict) else status
                if kind not in ("idle", "notLoaded"):
                    blockers.append({"kind": "active_thread" if kind == "active" else "unknown_thread", "thread_id": thread_id})
                if thread.get("ephemeral"):
                    blockers.append({"kind": "ephemeral_thread", "thread_id": thread_id})
                if await self.pages("thread/backgroundTerminals/list", {"threadId": thread_id}):
                    blockers.append({"kind": "background_commands", "thread_id": thread_id})
                if await self.pages("thread/queue/list", {"threadId": thread_id}):
                    blockers.append({"kind": "queued_work", "thread_id": thread_id})
                goal = await self.observer.request("thread/goal/get", {"threadId": thread_id})
                if "goal" not in goal:
                    raise ValueError("Native goal status unavailable")
                if goal["goal"] and goal["goal"].get("status") not in ("complete", "paused"):
                    blockers.append({"kind": "active_goal", "thread_id": thread_id})
        except Exception:
            blockers.append({"kind": "activity_unavailable"})
        # An in-flight control/response can settle during inspection. Read the
        # current journal again; admission has stayed closed throughout.
        for kind in ("inflight", "requests", "realtime", "processes", "logins"):
            if self.state[kind] and not any(row["kind"] == kind for row in blockers):
                blockers.append({"kind": kind, "count": len(self.state[kind])})
        return {'activity_version': 2, "draining": self.state["draining"], "ready": bool(self.state["draining"]) and not blockers,
                "blockers": blockers}

    async def admin_peer(self, reader, writer):
        try:
            async with asyncio.timeout(30):
                request = json.loads(await reader.readline())
                if request.get("schema") != SCHEMA or request.get("epoch") != self.epoch:
                    raise ValueError("Controller identity mismatch")
                method = request.get("method")
                if method == "begin_drain":
                    operation = request.get("operation_id")
                    if not isinstance(operation, str) or not 1 <= len(operation) <= 128:
                        raise ValueError("Missing recovery operation")
                    async with self.gate:
                        # A newer login may supersede an earlier operation.
                        # Taking ownership never reopens admission.
                        self.state["draining"] = operation
                        self.save()
                elif method == "prepare_stop":
                    async with self.gate:
                        if request.get("operation_id") != self.state["draining"]:
                            raise ValueError("Recovery ownership changed")
                        result = await self.status()
                        if result["ready"]:
                            self.state["sealed"] = True
                            self.save()
                        reply = {"result": result}
                elif method != "status":
                    raise ValueError("Unsupported control operation")
                if method != "prepare_stop":
                    reply = {"result": await self.status()}
        except Exception:
            reply = {"error": "Native controller operation unavailable"}
        try:
            writer.write((json.dumps({"schema": SCHEMA, "epoch": self.epoch, **reply}) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()


async def serve(options):
    gateway = NativeGateway(options.upstream, options.socket, options.control, options.epoch, managed_auth=options.managed_auth)
    done = asyncio.Event()
    install_signals(done.set)
    try:
        await gateway.start()
        await done.wait()
    finally:
        await gateway.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("upstream", "socket", "control", "epoch"):
        parser.add_argument("--" + key, required=True)
    parser.add_argument('--managed-auth', action='store_true', help='Require isolated Manager sign-in for auth mutations')
    asyncio.run(serve(parser.parse_args()))


if __name__ == "__main__":
    main()
