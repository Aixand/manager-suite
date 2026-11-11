"""Structural MCP connector, without importing MCP Manager."""
from __future__ import annotations
from .models import receipt, ConversationError
from .store import now


class LiveMcpConnector:
    def __init__(self, connection):
        self.connection = connection

    async def capabilities(self, target):
        return await self.connection._call("mcp.capabilities", {"target": target})

    async def inventory(self, target, *, scope, server_name=None):
        return await self.connection._call("mcp.inventory", {"target": target, "scope": scope, "server_name": server_name})

    async def reconnect(self, target, *, scope, operation_id, server_name=None, expected_selection_digest=None):
        return await self.connection._call("mcp.reconnect", dict(target=target, scope=scope,
            operation_id=operation_id, server_name=server_name, expected_selection_digest=expected_selection_digest))


class LocalMcpConnector:
    """Call-scoped attachment for explicit MODULE:factory consumers such as MCP CLI."""
    def __init__(self, options):
        self.options = dict(options)

    async def call(self, method, target, **params):
        from .client import Client
        from .transport import LocalConnector
        connection = await Client(self.options.get("home")).connect(target["connection_ref"],
            connector=LocalConnector(timeout=self.options.get("timeout", 30)))
        try:
            return await getattr(connection.mcp_connector(), method)(target, **params)
        finally:
            await connection.disconnect()

    async def capabilities(self, target):
        return await self.call("capabilities", target)

    async def inventory(self, target, *, scope, server_name=None):
        return await self.call("inventory", target, scope=scope, server_name=server_name)

    async def reconnect(self, target, *, scope, operation_id, server_name=None, expected_selection_digest=None):
        return await self.call("reconnect", target, scope=scope, operation_id=operation_id,
                               server_name=server_name, expected_selection_digest=expected_selection_digest)


def connector_factory(options):
    return LocalMcpConnector(options)


def check_target(core, target):
    if target.get("schema") != "mcp-manager/runtime-target-1" or target.get("connector_id") != "conversation-manager" or target.get("backend_id") != core.ref["backend_id"] or target.get("connection_ref") != core.ref or target.get("runtime_ref") != core.ref["runtime_ref"]:
        raise ConversationError("conflict", "MCP target does not name this exact connector/connection/runtime")
    session = next((s for s in core.sessions.values() if s["native_session_id"] == target.get("native_session_id")), None)
    if target.get("native_session_id") is not None and session is None:
        raise ConversationError("conflict", "Unknown native session target")
    mcp = (core.store.get("session", session["session_id"])["settings"].get("mcp") or {}) if session else (core.prepared.settings.get("mcp") or {})
    for key in ("selection_ref", "selection_digest", "prepared_generations"):
        if target.get(key, [] if key == "prepared_generations" else None) != mcp.get(key, [] if key == "prepared_generations" else None):
            raise ConversationError("conflict", "MCP selection or prepared generation identity differs from retained settings")
    return session, mcp


async def worker_mcp(core, method, params):
    target = params["target"]
    session, selection = check_target(core, target)
    fixture = getattr(core.adapter, "fixture", {}).get("mcp", False)
    native = core.prepared.protocol == "codex-app-server"
    caps = dict(schema="mcp-manager/runtime-capabilities-1", connector_id="conversation-manager", backend_id=core.ref["backend_id"],
        inventory=bool(fixture or native), reconnect=bool(fixture),
        inventory_scopes=["session"] if fixture or native else [], reconnect_scopes=["session"] if fixture else [],
        server_filter=bool(fixture or native), live_updates=False, preserve_selection=bool(fixture),
        evidence={"adapter_id": core.ref["adapter_id"], "adapter_version": core.adapter.adapter_version,
                  "protocol_version": core.adapter.protocol_version, "fixture_extension": bool(fixture),
                  "native_reload_scope": "process" if native else None}, observed_at=now())
    if method == "capabilities":
        return caps
    scope, server_name = params["scope"], params.get("server_name")
    if scope not in ("server", "session", "process") or scope == "server" and not server_name:
        raise ConversationError("validation", "Explicit valid scope and server_name for server scope are required")
    if scope == "session" and not session:
        raise ConversationError("validation", "Session scope requires a native session target")
    if method == "inventory":
        if scope not in caps["inventory_scopes"] or server_name and not caps["server_filter"]:
            raise ConversationError("unsupported", "Inventory scope/filter is unsupported")
        if core.lost:
            raise ConversationError("transport", "Live MCP transport unavailable")
        if fixture:
            result = await core.adapter.agent.ext_method("fixture/mcp_inventory", {"sessionId": session["native_session_id"]})
            servers = result["servers"]
        else:
            servers, cursor, seen = [], None, set()
            while True:
                page = await core.adapter.rpc("mcpServerStatus/list", {"threadId": session["native_session_id"], **({"cursor": cursor} if cursor else {})})
                states = {"connected": "connected", "starting": "connecting", "failed": "failed",
                          "cancelled": "disconnected", "disabled": "disconnected"}
                servers.extend({"name": r["name"], "native_name": r["name"],
                    "connection_status": states.get(r.get("runtimeStatus"), "unknown"), "auth_status": r.get("authStatus", "unknown"),
                    "tools": r.get("tools"), "definition_state": "unknown", "preparation_state": "unknown", "native": r} for r in page["data"])
                cursor = page.get("nextCursor")
                if not cursor:
                    break
                if cursor in seen:
                    raise ConversationError("transport", "Native MCP pagination loop")
                seen.add(cursor)
        return dict(schema="mcp-manager/runtime-inventory-1", target=target, scope=scope,
            selection_ref=target.get("selection_ref"), selection_digest=target.get("selection_digest"),
            prepared_generations=target.get("prepared_generations", []),
            servers=[s for s in servers if not server_name or s["name"] == server_name], live_updates=False, observed_at=now())
    if method != "reconnect":
        raise ConversationError("unsupported", "Unknown live MCP method")
    if params.get("expected_selection_digest") != target.get("selection_digest") or not target.get("selection_digest"):
        raise ConversationError("conflict", "Reconnect requires the retained expected_selection_digest")
    operation_id = params["operation_id"]
    row, fresh = core.store.claim(operation_id, {"method": "mcp.reconnect", "params": params}, {"target": target})
    if not fresh and row.get("result"):
        return row["result"]
    result = dict(schema="mcp-manager/runtime-reconnect-1", operation_id=operation_id, status="unsupported",
        target=target, requested_scope=scope, actual_scope=None, affected_targets=None,
        selection_before=selection, selection_after=None, selection_preserved=None, inventory=None,
        error=None, recovery={"target": target, "connection_ref": core.ref})
    if not fresh:
        result.update(status="uncertain", error=row["error"])
        return result
    if scope not in caps["reconnect_scopes"] or not caps["preserve_selection"]:
        result["error"] = ConversationError("unsupported", "No verified selection-preserving reconnect at requested scope").to_dict()
    else:
        pending_result = {**result, "status": "uncertain", "error": ConversationError("uncertain",
            "Reconnect dispatch/completion unconfirmed", uncertain=True, operation_id=operation_id).to_dict()}
        core.store.finish(receipt(operation_id, "uncertain", result=pending_result,
                                error=pending_result["error"], recovery=result["recovery"]))
        try:
            if core.lost:
                raise ConversationError("transport", "Live transport unavailable")
            response = await core.adapter.agent.ext_method("fixture/mcp_reconnect", {"sessionId": session["native_session_id"],
                "selectionDigest": target["selection_digest"], "preparedGenerations": target.get("prepared_generations", [])})
            result.update(status="completed", actual_scope=scope, affected_targets=[target], selection_after={**selection,
                "selection_digest": response.get("selectionDigest"), "prepared_generations": response.get("preparedGenerations")},
                          selection_preserved=response.get("selectionDigest") == target["selection_digest"] and
                          response.get("preparedGenerations") == target.get("prepared_generations", []))
            if not result["selection_preserved"]:
                raise ConversationError("integrity", "Connector did not preserve the recorded selection")
            result["inventory"] = await worker_mcp(core, "inventory", {"target": target, "scope": scope, "server_name": server_name})
        except Exception as exc:
            error = core.error(exc, operation_id, dispatched=True)
            result.update(status="uncertain" if error.uncertain else "failed", error=error.to_dict())
    core.store.finish(receipt(operation_id, result["status"], result=result, error=result["error"], recovery=result["recovery"]))
    return result
