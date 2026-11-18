# Apply authorization, secrets, and limits

Keep execution authorization in the consuming application and server. MCP Manager
validates configuration and connector identity; it does not grant access to a
server's resources, prompts, or tools.

## Keep secret values outside retained records

Store variable names such as `DOCS_TOKEN` in `env_vars`. The resolved definition
retains those names, not their values. Your client supplies the authorized values
when it starts the process. Do not put credentials in args, target references,
factory options, source URLs, or arbitrary native details: these can enter records
or logs. MCP Manager does not promise automatic redaction of arbitrary content.

A connector must verify that opaque connection/runtime/session references identify
its retained runtime and enforce the application's authorization policy. A matching
selection digest is a concurrency check, not an authorization token. Inventory
`auth_status` is an observation from the adapter, not an authentication flow.
HTTP/OAuth and private GitHub acquisition are not supplied by this product.

The internal preparation probe starts its child with the inherited process
environment; `env_vars` is not a sandbox or an environment isolation boundary.
Installation runs package code; review provenance and recipes before authorizing it.
Use [repository security reporting](../../SECURITY.md) for vulnerabilities.

## Input and execution limits

| Input | Current constraint |
| --- | --- |
| Selection | At most 64 supplied names, then first-occurrence deduplication |
| Definition fields | `kind`, `command`, `args`, `env_vars`, `cwd`, startup/tool timeout, `installation` |
| Arguments | At most 128 strings; no NUL bytes |
| Environment references | Variable names matching `[A-Za-z_][A-Za-z0-9_]*` |
| Startup/tool timeouts | Default 60 seconds each; greater than 0 and at most 3600; booleans rejected |
| Reconnect operation ID | Nonempty string, at most 128 characters |
| Preparation job listing | Newest 50 records |
| Probe reader | 32-message queue; line reads bounded to 1 MiB |

Timeout fields in resolved definitions require enforcement by the consuming
runtime. The internal probe uses `startup_timeout_sec` as each request's deadline;
it is not a general `tool_timeout_sec` enforcement layer. Live connector calls have
no built-in universal deadline; adapters must implement suitable bounds.

See [validation](../src/mcp_manager/library/definitions.py),
[probe limits](../src/mcp_manager/diagnostics/stdio.py),
[live validation](../src/mcp_manager/live/service.py), and
[secret-reference tests](../tests/test_store.py).
