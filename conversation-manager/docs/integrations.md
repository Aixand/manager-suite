# Integrate providers and resources

Use Agent Manager for environment administration and Conversation Manager for
chat protocols. Discover capabilities from the connected worker before enabling
provider-specific actions.

## Choose the owning layer

| Task | Owner | Boundary |
| --- | --- | --- |
| Install, authenticate, select native environment | Agent Manager | Targets are observations, not leases or credential grants |
| Start and control a chat | Conversation Manager | Retained worker, requests, receipts and events |
| Read models/accounts | Agent Manager | Startup support is not model entitlement |
| Execute native TUI | Agent Manager and Undercurrent | Separate surface; no implied attachment to a chat |
| Prepare instructions, skills or MCP | Adapter | Startup delivery; content libraries and reconciliation remain upstream |
| Render settings and slash choices | Frontend | Use actual advertised values; never silently prompt unsupported commands |

## Native startup and SDK adapters

Codex and OpenCode use the high-level frontend startup API. Both have implemented
local startup on Linux, macOS and Windows; qualification remains version/platform
specific. Other adapters require explicit prepared launch inputs.

| Protocol | Adapter and optional extra |
| --- | --- |
| `claude-agent-sdk` | `claude-sdk` |
| `copilot-sdk` | `copilot-sdk` |
| `antigravity-sdk` | `antigravity-sdk` |

Provider SDKs own native sessions and provider interaction. Conversation Manager
normalizes callbacks and retains lifecycle evidence. Imports are lazy; installing
an extra does not sign in. The built-in fixture explicitly replaces the SDK driver
and establishes no authenticated provider support.

## Retain the native environment

The worker revalidates the captured Agent Manager environment revision and retains
its lease. Detaching does not release that lease. Separate Codex uses stdio;
OpenCode owns a private authenticated HTTP server with exclusive environment use.
OpenCode chats in the same store/environment reuse its worker with distinct native
session identities, cwd and settings. Conflicting server-wide MCP selections fail.
OpenCode native skill delivery remains unqualified.

Native tool permission rules are not an OS sandbox. Account access and applicable
service terms remain separate from successful startup. The local path does not
implement hosted/commercial Sign in with ChatGPT, remote execution or automatic
cross-environment history transfer.

## Connect retained MCP resources

`await conversation.mcp_target()` describes its exact retained selection and
runtime. Use `conversation.connection.mcp_connector()` with MCP Manager's public
runtime APIs. Captured configuration does not establish live connectivity or
qualified reconnect support; check connector inventory and capabilities.

[Full provider reference](provider-reference.md) retains native launch examples,
credential filtering, lease details, model variants and resource limitations.
[Adapter registry](../src/conversation_manager/adapters/registry.py),
[Agent Manager bridge](../src/conversation_manager/agents.py),
[SDK runtime](../src/conversation_manager/sdk_runtime.py), and
[MCP target implementation](../src/conversation_manager/mcp.py) own these boundaries.
