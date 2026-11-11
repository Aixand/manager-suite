"""Structural connector contracts; no backend implementation dependencies."""
from __future__ import annotations
from typing import Any, Protocol, TypedDict


class McpTarget(TypedDict, total=False):
    schema: str
    connector_id: str
    backend_id: str
    connection_ref: dict
    runtime_ref: dict
    native_session_id: str | None
    selection_ref: Any
    selection_digest: str | None
    prepared_generations: list | dict


class McpCapabilities(TypedDict):
    schema: str
    connector_id: str
    backend_id: str
    inventory: bool
    reconnect: bool
    inventory_scopes: list[str]
    reconnect_scopes: list[str]
    server_filter: bool
    live_updates: bool
    preserve_selection: bool
    evidence: dict
    observed_at: Any


class McpInventory(TypedDict):
    schema: str
    target: McpTarget
    scope: str
    selection_ref: Any
    selection_digest: str | None
    prepared_generations: Any
    servers: list[dict]
    live_updates: bool
    observed_at: Any


class McpReconnectResult(TypedDict):
    schema: str
    operation_id: str
    status: str
    target: McpTarget
    requested_scope: str
    actual_scope: str | None
    affected_targets: list | None
    selection_before: dict | None
    selection_after: dict | None
    selection_preserved: bool | None
    inventory: McpInventory | None
    error: dict | None
    recovery: dict | None


class McpConnector(Protocol):
    async def capabilities(self, target: McpTarget) -> McpCapabilities: ...
    async def inventory(self, target: McpTarget, *, scope: str,
                        server_name: str | None = None) -> McpInventory: ...
    async def reconnect(self, target: McpTarget, *, scope: str, operation_id: str,
                        server_name: str | None = None,
                        expected_selection_digest: str | None = None) -> McpReconnectResult: ...

