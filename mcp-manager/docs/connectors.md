# Live MCP connector contract

MCP Manager exports `McpConnector`, `McpTarget`, `McpCapabilities`, `McpInventory`
and `McpReconnectResult`. These describe JSON mappings and a structural protocol.
An adapter can implement the interface without installing MCP Manager. Packaged
JSON schemas are available through `importlib.resources.files('mcp_manager') /
'schemas/runtime.json'` (JSON Schema 2020-12, named `$defs`).

```python
async def capabilities(target): ...
async def inventory(target, *, scope, server_name=None): ...
async def reconnect(target, *, scope, operation_id, server_name=None,
                    expected_selection_digest=None): ...

caps = await manager.runtime_capabilities(connector, target)
live = await manager.runtime_status(connector, target, scope="session")
receipt = await manager.reconnect(connector, target, scope="session",
    operation_id="opaque-intent-id", expected_selection_digest=target["selection_digest"])
retained = manager.runtime_operation("opaque-intent-id")
```

The CLI explicitly loads `MODULE:factory`, calling the synchronous `factory(options)`
with a JSON object. Factory options must contain credential references, not secrets
that will be logged. Connector instances, services and resolved environment values
are never serialized by MCP Manager. An application resolves its own project/chat
IDs before creating the target. No application database or concrete backend class is
required. The consumer owns the connector's connection lifetime.

## Target and capability discovery

A target uses `schema: mcp-manager/runtime-target-1`. Required identity fields are
nonempty `connector_id`, `backend_id`, opaque object `connection_ref` and opaque
object `runtime_ref`. `native_session_id` is optional but required for session
scope. `selection_ref`, `selection_digest`, and `prepared_generations` identify the
saved selection; omitted values normalize to null, null, and an empty array.
Generations may be a JSON array or mapping, as supplied by the adapter. Opaque
references must exclude raw credentials. The connector must validate that these
references name its exact retained runtime, connection and session. MCP Manager
checks the returned connector/backend identities and the returned target.

Capabilities use `schema: mcp-manager/runtime-capabilities-1`, `connector_id`,
`backend_id`, boolean `inventory`, `reconnect`, `server_filter`, `live_updates`,
`preserve_selection`, arrays `inventory_scopes` and `reconnect_scopes`, `evidence`
and `observed_at`. Scopes are `server`, `session`, or `process`; omitted/false flags
mean unsupported. Evidence should identify the adapter version, protocol version
and verified preservation mechanism. An adapter must advertise preservation only
when it can preserve the selected configuration and all relevant generation
identities at the requested scope. Basic ACP support alone establishes neither
inventory nor reconnect support.

## Inventory and selection observations

Inventory uses `schema: mcp-manager/runtime-inventory-1` and exact `target`, `scope`,
`selection_ref`, `selection_digest`, `prepared_generations`, `servers`, `live_updates`
and `observed_at`. Selection metadata must describe the observed retained selection,
not an arbitrary current catalog. Missing observations remain null/unknown.

Rows contain `name`, `native_name`, `connection_status`, `auth_status`, `tools`,
`definition_state`, `preparation_state` and `native`. Connection states normalize
to `connected`, `connecting`, `failed`, `disconnected` or `unknown`. Unrecognized
states remain unknown. Missing tools are null, never an invented empty inventory.
Native details and observed tools are retained. Registration/preparation alone never
implies a connected server. Filtering is available only when advertised; `server`
scope requires `server_name`. A process-scope filter leaves actual scope as process.

Selection observations in receipts are mappings with `selection_ref`,
`selection_digest` and `prepared_generations`; additional adapter metadata is allowed.
The manager compares these three identities, not a connector's asserted boolean.

## Reconnect intent, scope and uncertainty

Reconnect requires a nonempty `expected_selection_digest` matching the target, an
explicit supported scope, a selection-preserving capability, and inventory support
at that scope/filter. The adapter must enforce the same guard at dispatch, since
preflight and mutation are separate observations. Preflight inventory must match
the target selection and generations. A stale selection is a conflict before
dispatch. No requested scope is silently widened. Native Codex reload is process
scoped and requires installed-version evidence before preservation is advertised.

A result uses `schema: mcp-manager/runtime-reconnect-1` with `operation_id`, `status`,
`target`, `requested_scope`, `actual_scope`, `affected_targets`, `selection_before`,
`selection_after`, `selection_preserved`, `inventory`, `error` and `recovery`.
`affected_targets` is an array of known references, or null when unknown. Process
operations report every affected target the adapter can enumerate.

`status` is `accepted`, `completed`, `failed`, `unsupported` or `uncertain`.
Accepted means acknowledgment, with selection/connection observations possibly
unknown. Completed requires observed matching selection/generation identities;
its inventory can still contain failed server connections. An observed changed
selection becomes an integrity failure. A connector that reports a wider actual
scope produces uncertainty with that reported scope and affected targets retained.
A malformed/lost response after possible dispatch produces uncertainty. Adapters
should return explicit failed receipts for observed failures; an exception after
entering the connector mutation cannot establish that nothing was dispatched.

Operation IDs are opaque nonempty UTF-8 strings of at most 128 characters, scoped
to this MCP store, and safely hashed for filenames. MCP Manager stores the request
and uncertain receipt under `runtime-operations/` before invoking reconnect.
Returning a completed/failed/accepted receipt finalizes the record. Reusing the
same normalized request returns that receipt (or pending uncertainty) without
contacting the backend. Changed arguments under the same key conflict. Observation
fields `observed_at`, `created_at` and `updated_at` are excluded from intent identity.
Connector options/services and resolved credentials do not enter the intent.

Cancellation, client exit or a lost final local write leaves the reservation
uncertain; no automatic replay or replacement process occurs. `runtime_operation`
allows inspection without a live connector. Read-only observations and unsupported
preflight do not create a store or reserve a key; those preflight failures may be
corrected safely. This local ledger complements backend at-most-once semantics;
it cannot make an upstream mutation idempotent. No refresh, assignment change or
hot attachment is performed by reconnect.

Errors expose `code`, `message`, `uncertain`, `operation_id` and `recovery` through
`McpError.to_dict()`. Structural backend errors with these attributes translate
without importing their exception class. Recovery always retains the original
target/operation for uncertain reconnect. Treat references as private local data;
do not include raw credentials in targets, inventory/native details or receipts.
