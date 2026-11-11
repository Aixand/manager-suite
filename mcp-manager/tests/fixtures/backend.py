"""Injected MCP backend fixture; no process, provider or indexing side effects."""
import copy
import asyncio


class Connector:
    def __init__(self, options=None):
        self.options = options or {}
        self.calls = []
        self.dispatched = asyncio.Event()
        self.release = asyncio.Event()

    async def capabilities(self, target):
        self.calls.append(("capabilities", copy.deepcopy(target)))
        scopes = self.options.get("scopes", ["server", "session", "process"])
        return {"schema": "mcp-manager/runtime-capabilities-1", "connector_id": target["connector_id"],
            "backend_id": target["backend_id"], "inventory": self.options.get("inventory", True),
            "reconnect": self.options.get("reconnect", True), "inventory_scopes": scopes,
            "reconnect_scopes": scopes, "server_filter": self.options.get("server_filter", True),
            "live_updates": False, "preserve_selection": self.options.get("preserve_selection", True),
            "evidence": {"adapter_version": "fixture-1", "protocol_version": "fixture-1"}, "observed_at": "fixture-time"}

    async def inventory(self, target, *, scope, server_name=None):
        self.calls.append(("inventory", scope, server_name))
        return {"schema": "mcp-manager/runtime-inventory-1", "target": copy.deepcopy(target),
            "scope": self.options.get("inventory_scope", scope), "selection_ref": target["selection_ref"],
            "selection_digest": self.options.get("selection_digest", target["selection_digest"]),
            "prepared_generations": self.options.get("generations", target["prepared_generations"]),
            "servers": [{"name": "docs", "native_name": "agent_manager_docs", "connection_status": "failed",
                         "auth_status": "unknown", "tools": [{"name": "search"}], "definition_state": "configured",
                         "preparation_state": "prepared", "native": {"observed": True}},
                        {"name": "external", "native_name": "external", "connection_status": "novel-status"}],
            "live_updates": False, "observed_at": "fixture-time"}

    async def reconnect(self, target, *, scope, operation_id, server_name=None, expected_selection_digest=None):
        self.calls.append(("reconnect", scope, operation_id, server_name, expected_selection_digest))
        self.dispatched.set()
        if self.options.get("block"):
            await self.release.wait()
        if self.options.get("lost_reply"):
            raise ConnectionError("fixture receipt lost")
        before = {key: copy.deepcopy(target[key]) for key in ("selection_ref", "selection_digest", "prepared_generations")}
        after = copy.deepcopy(before)
        if self.options.get("change_generation"):
            after["prepared_generations"] = [{"name": "sonar", "generation": "replacement"}]
        status = self.options.get("status", "completed")
        return {"schema": "mcp-manager/runtime-reconnect-1", "operation_id": operation_id, "status": status,
            "target": copy.deepcopy(target), "requested_scope": scope, "actual_scope": self.options.get("actual_scope", scope),
            "affected_targets": [target] if scope != "process" else [target, {"native_session_id": "another-session"}],
            "selection_before": before, "selection_after": after if status == "completed" else None,
            "selection_preserved": True,  # Manager must verify this against actual identities.
            "inventory": await self.inventory(target, scope=scope, server_name=server_name) if status == "completed" else None,
            "error": None, "recovery": {"target": target, "operation_id": operation_id}}


def factory(options):
    return Connector(options)
