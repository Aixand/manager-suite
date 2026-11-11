"""Structural live MCP operations; no backend or application imports.

Reconnect intents are durable before dispatch. Only an injected connector can
validate native selection and perform the backend operation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json

from ..errors import McpError
from ..library.definitions import digest
from ..storage.records import read, write
from ..storage.files import locks, operational


from .contracts import McpTarget, McpCapabilities, McpInventory, McpReconnectResult, McpConnector


def json_copy(value, *, code="validation"):
    def check(item):
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON keys must be strings")
            for key, child in item.items():
                key.encode("utf-8")
                check(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                check(child)
        elif isinstance(item, str):
            item.encode("utf-8")
    try:
        check(value)
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        encoded.encode("utf-8")
        return json.loads(encoded)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise McpError(code, "Expected finite UTF-8 JSON with string keys") from error


def target_record(value):
    target = json_copy(value)
    if not isinstance(target, dict) or target.get("schema") != "mcp-manager/runtime-target-1":
        raise McpError("validation", "Expected mcp-manager/runtime-target-1 target")
    for key in ("connector_id", "backend_id"):
        if not isinstance(target.get(key), str) or not target[key]:
            raise McpError("validation", f"Target requires {key}")
    for key in ("connection_ref", "runtime_ref"):
        if not isinstance(target.get(key), dict) or not target[key]:
            raise McpError("validation", f"Target requires an opaque {key} object")
    native = target.get("native_session_id")
    if native is not None and (not isinstance(native, str) or not native):
        raise McpError("validation", "Native session identity must be a nonempty string")
    target.setdefault("selection_ref", None)
    target.setdefault("selection_digest", None)
    target.setdefault("prepared_generations", [])
    if not isinstance(target["prepared_generations"], (dict, list)):
        raise McpError("validation", "Prepared generations must be an object or array")
    selection_digest = target["selection_digest"]
    if selection_digest is not None and (not isinstance(selection_digest, str) or not selection_digest):
        raise McpError("validation", "Selection digest must be a nonempty string")
    return target


def scope_check(target, scope, server_name):
    if scope not in ("server", "session", "process"):
        raise McpError("validation", "Scope must be server, session or process")
    if server_name is not None and (not isinstance(server_name, str) or not server_name):
        raise McpError("validation", "Server name must be a nonempty string")
    if scope == "server" and not server_name:
        raise McpError("validation", "Server scope requires server_name")
    if scope == "session" and not target.get("native_session_id"):
        raise McpError("validation", "Session scope requires a native session target")


def external_error(error, *, operation_id=None):
    """Accept structural backend errors without depending on their concrete class."""
    if isinstance(error, McpError):
        return error
    code = getattr(error, "code", None)
    if code in {"validation", "not_found", "conflict", "integrity", "io", "unsupported", "busy",
                "transport", "timeout", "uncertain"}:
        return McpError(code, getattr(error, "message", "Backend operation failed"),
                        uncertain=bool(getattr(error, "uncertain", False)),
                        operation_id=operation_id, recovery=getattr(error, "recovery", None))
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return McpError("timeout", "Backend observation timed out", operation_id=operation_id)
    return McpError("transport", "Backend connection failed; inspect the original target",
                    operation_id=operation_id)


async def invoke(connector, method, target, **kwargs):
    callback = getattr(connector, method, None)
    if not callable(callback):
        raise McpError("unsupported", f"Connector does not implement {method}", recovery={"target": target})
    try:
        return json_copy(await callback(json_copy(target), **kwargs), code="integrity")
    except Exception as error:
        raise external_error(error, operation_id=kwargs.get("operation_id")) from error


async def capabilities(connector, target):
    target = target_record(target)
    row = await invoke(connector, "capabilities", target)
    if not isinstance(row, dict) or row.get("schema") != "mcp-manager/runtime-capabilities-1":
        raise McpError("integrity", "Connector returned invalid capabilities")
    if any(row.get(key) != target[key] for key in ("connector_id", "backend_id")):
        raise McpError("conflict", "Capabilities identify a different connector/backend")
    result = {"schema": row["schema"], "connector_id": row["connector_id"], "backend_id": row["backend_id"]}
    for key in ("inventory", "reconnect", "server_filter", "live_updates", "preserve_selection"):
        result[key] = row.get(key) is True
    for key in ("inventory_scopes", "reconnect_scopes"):
        scopes = row.get(key, [])
        if not isinstance(scopes, list) or any(scope not in ("server", "session", "process") for scope in scopes):
            raise McpError("integrity", "Connector advertised invalid scopes")
        result[key] = list(dict.fromkeys(scopes))
    result["evidence"] = row.get("evidence", {})
    result["observed_at"] = row.get("observed_at")
    if not isinstance(result["evidence"], dict):
        raise McpError("integrity", "Capability evidence must be an object")
    return result


def supported(caps, method, scope, server_name):
    return caps[method] and scope in caps[method + "_scopes"] and (not server_name or caps["server_filter"])


def inventory_record(value, target, scope, server_name=None):
    if not isinstance(value, dict) or value.get("schema") != "mcp-manager/runtime-inventory-1":
        raise McpError("integrity", "Connector returned invalid inventory")
    if value.get("scope") != scope:
        raise McpError("integrity", "Connector changed the requested inventory scope")
    if target_record(value.get("target")) != target:
        raise McpError("conflict", "Inventory identifies a different target")
    rows = value.get("servers", [])
    if not isinstance(rows, list):
        raise McpError("integrity", "Inventory servers must be an array")
    servers = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"]:
            raise McpError("integrity", "Inventory server requires a name")
        if server_name and server_name not in (row["name"], row.get("native_name")):
            continue
        state = row.get("connection_status", "unknown")
        servers.append({**row, "native_name": row.get("native_name"),
            "connection_status": state if state in ("connected", "connecting", "failed", "disconnected", "unknown") else "unknown",
            "auth_status": row.get("auth_status", "unknown"), "tools": row.get("tools"),
            "definition_state": row.get("definition_state", "unknown"),
            "preparation_state": row.get("preparation_state", "unknown"), "native": row.get("native")})
    return {"schema": value["schema"], "target": target, "scope": scope,
            "selection_ref": value.get("selection_ref"), "selection_digest": value.get("selection_digest"),
            "prepared_generations": value.get("prepared_generations"), "servers": servers,
            "live_updates": value.get("live_updates") is True, "observed_at": value.get("observed_at")}


async def inventory(connector, target, *, scope="session", server_name=None):
    target = target_record(target)
    scope_check(target, scope, server_name)
    caps = await capabilities(connector, target)
    if not supported(caps, "inventory", scope, server_name):
        raise McpError("unsupported", "Connector cannot inspect the requested scope/filter",
                       recovery={"target": target, "scope": scope})
    return inventory_record(await invoke(connector, "inventory", target, scope=scope, server_name=server_name),
                            target, scope, server_name)


def selection(record):
    return {key: record.get(key) for key in ("selection_ref", "selection_digest", "prepared_generations")}


def operation_path(manager, operation_id):
    if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 128:
        raise McpError("validation", "Operation ID must be a nonempty string of at most 128 characters")
    json_copy(operation_id)
    key = hashlib.sha256(operation_id.encode()).hexdigest()
    return manager.root / "runtime-operations" / (key + ".json"), key


@operational
def operation(manager, operation_id):
    path, _ = operation_path(manager, operation_id)
    row = read(path)
    if row is None:
        raise McpError("not_found", "Reconnect operation not found")
    return retained_record(row, operation_id)["result"]


def retained_record(row, operation_id):
    if (not isinstance(row, dict) or row.get("schema") != "mcp-manager/runtime-operation-1"
            or not isinstance(row.get("signature"), str) or not isinstance(row.get("request"), dict)
            or not isinstance(row.get("result"), dict)
            or row["result"].get("schema") != "mcp-manager/runtime-reconnect-1"
            or row["result"].get("operation_id") != operation_id):
        raise McpError("integrity", "Invalid retained reconnect record; inspect the original target; do not replay",
                       operation_id=operation_id, recovery={"operation_id": operation_id})
    return row


def intent(value):
    if isinstance(value, dict):
        return {key: intent(child) for key, child in value.items()
                if key not in ("observed_at", "created_at", "updated_at")}
    if isinstance(value, list):
        return [intent(child) for child in value]
    return value


def receipt(target, scope, operation_id, status, error=None, **fields):
    return {"schema": "mcp-manager/runtime-reconnect-1", "operation_id": operation_id,
        "status": status, "target": target, "requested_scope": scope, "actual_scope": None,
        "affected_targets": None, "selection_before": selection(target), "selection_after": None,
        "selection_preserved": None, "inventory": None,
        "error": error.to_dict() if isinstance(error, McpError) else error,
        "recovery": {"target": target, "operation_id": operation_id}, **fields}


async def reconnect(manager, connector, target, *, scope, operation_id, server_name=None,
                    expected_selection_digest=None):
    target = target_record(target)
    scope_check(target, scope, server_name)
    path, key = operation_path(manager, operation_id)
    request = {"target": intent(target), "scope": scope, "server_name": server_name,
               "expected_selection_digest": expected_selection_digest}
    signature = digest(json_copy(request))
    existing = read(path)
    if existing is not None:
        retained_record(existing, operation_id)
        if existing["signature"] != signature:
            raise McpError("conflict", "Reconnect key was reused with different arguments", operation_id=operation_id)
        return existing["result"]
    if not expected_selection_digest or expected_selection_digest != target["selection_digest"]:
        raise McpError("conflict", "Reconnect requires the retained expected_selection_digest", operation_id=operation_id)
    caps = await capabilities(connector, target)
    if (not supported(caps, "reconnect", scope, server_name) or not caps["preserve_selection"]
            or not supported(caps, "inventory", scope, server_name)
            or not callable(getattr(connector, "reconnect", None))):
        return receipt(target, scope, operation_id, "unsupported", McpError("unsupported",
            "No verified selection-preserving reconnect with inventory at the requested scope/filter",
            operation_id=operation_id, recovery={"target": target}))
    before = inventory_record(await invoke(connector, "inventory", target, scope=scope, server_name=server_name),
                              target, scope, server_name)
    if selection(before) != selection(target):
        raise McpError("conflict", "Observed selection or prepared generations differ from the target",
                       operation_id=operation_id, recovery={"target": target})
    pending = receipt(target, scope, operation_id, "uncertain", McpError("uncertain",
        "Reconnect delivery is unconfirmed; inspect this operation and the original target; do not replay",
        uncertain=True, operation_id=operation_id, recovery={"target": target}), inventory=before)
    # No await inside the lock. Other callers can inspect this reservation while
    # dispatch is pending; a crashed/cancelled client leaves it uncertain.
    try:
        with locks(manager.root / ".locks", "runtime-" + key, wait=True):
            existing = read(path)
            if existing is not None:
                retained_record(existing, operation_id)
                if existing["signature"] != signature:
                    raise McpError("conflict", "Reconnect key was reused with different arguments", operation_id=operation_id)
                return existing["result"]
            write(path, {"schema": "mcp-manager/runtime-operation-1", "signature": signature,
                         "request": request, "result": pending})
    except OSError as error:
        raise McpError("io", "Cannot retain reconnect intent; no dispatch occurred", operation_id=operation_id) from error
    try:
        raw = await invoke(connector, "reconnect", target, scope=scope, operation_id=operation_id,
                           server_name=server_name, expected_selection_digest=expected_selection_digest)
        result = reconnect_record(raw, target, scope, operation_id, server_name)
    except asyncio.CancelledError:
        raise  # The durable uncertain reservation survives client cancellation.
    except Exception as error:
        reason = external_error(error, operation_id=operation_id)
        # Even a connector exception may occur after dispatch. Explicit receipts
        # are needed to establish failure; never infer safe replay from an exception.
        result = {**pending, "error": McpError("uncertain", reason.message, uncertain=True,
                  operation_id=operation_id, recovery={"target": target}).to_dict()}
    try:
        with locks(manager.root / ".locks", "runtime-" + key, wait=True):
            write(path, {"schema": "mcp-manager/runtime-operation-1", "signature": signature,
                         "request": request, "result": result})
    except (OSError, McpError):
        return receipt(target, scope, operation_id, "uncertain", McpError("uncertain",
            "Reconnect result could not be retained; inspect the original backend target",
            uncertain=True, operation_id=operation_id, recovery={"target": target}))
    return result


def reconnect_record(raw, target, scope, operation_id, server_name):
    if (not isinstance(raw, dict) or raw.get("schema") != "mcp-manager/runtime-reconnect-1"
            or raw.get("operation_id") != operation_id or target_record(raw.get("target")) != target
            or raw.get("requested_scope") != scope):
        raise McpError("integrity", "Connector returned a receipt for a different operation/target")
    status = raw.get("status")
    if status not in ("accepted", "completed", "failed", "unsupported", "uncertain"):
        raise McpError("integrity", "Connector returned an invalid reconnect status")
    actual = raw.get("actual_scope")
    if actual is not None and actual != scope:
        return receipt(target, scope, operation_id, "uncertain", McpError("uncertain",
            "Connector changed the requested reconnect scope; inspect the reported affected targets",
            uncertain=True, operation_id=operation_id, recovery={"target": target}),
            actual_scope=actual, affected_targets=raw.get("affected_targets"))
    if status in ("accepted", "completed") and actual != scope:
        raise McpError("integrity", "Reconnect acknowledgment must report its actual scope")
    before, after = raw.get("selection_before"), raw.get("selection_after")
    if before is not None and (not isinstance(before, dict) or selection(before) != selection(target)):
        raise McpError("integrity", "Reconnect receipt changed the original selection")
    preserved = None
    if after is not None:
        if not isinstance(after, dict):
            raise McpError("integrity", "Selection observation must be an object")
        preserved = selection(after) == selection(target)
    if status == "completed" and preserved is None:
        raise McpError("integrity", "Completed reconnect has no observed selection/generation identity")
    error = raw.get("error")
    if isinstance(error, dict) and error.get("uncertain") is True:
        status = "uncertain"
    if preserved is False:
        status = "failed"
        error = McpError("integrity", "Reconnect did not preserve the selected configuration/generations",
                         operation_id=operation_id, recovery={"target": target}).to_dict()
    if status in ("failed", "unsupported", "uncertain") and not isinstance(error, dict):
        error = McpError("uncertain" if status == "uncertain" else "unsupported" if status == "unsupported" else "transport",
                         "Inspect the connector and original target", uncertain=status == "uncertain",
                         operation_id=operation_id, recovery={"target": target}).to_dict()
    elif isinstance(error, dict):
        error = McpError(error.get("code", "uncertain" if status == "uncertain" else "transport"),
                         error.get("message", "Inspect the connector and original target"),
                         uncertain=status == "uncertain", operation_id=operation_id,
                         recovery=error.get("recovery") or {"target": target}).to_dict()
    affected = raw.get("affected_targets")
    if affected is not None and not isinstance(affected, list):
        raise McpError("integrity", "Affected targets must be an array or null")
    observed = raw.get("inventory")
    if observed is not None:
        observed = inventory_record(observed, target, scope, server_name)
        if preserved is True and selection(observed) != selection(target):
            raise McpError("integrity", "Reconnect inventory contradicts selection preservation")
    return receipt(target, scope, operation_id, status, actual_scope=actual, affected_targets=affected,
                   selection_before=before, selection_after=after, selection_preserved=preserved,
                   inventory=observed, error=error,
                   recovery=raw.get("recovery") or {"target": target, "operation_id": operation_id})
