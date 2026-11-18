# Provider integration reference

Keep provider administration outside the chat protocol. Use advertised capabilities before exposing backend-specific controls.


Agent Manager owns installation, profiles, authentication, native environments,
model catalogs, and account observations. Conversation Manager owns conversation
protocols and live conversation controls, including session settings and commands.
Frontends can also call Agent Manager directly for administration and native TUI
execution through Undercurrent. A native TUI execution is separate from a managed
chat conversation; switching surfaces does not imply attachment to the same process.

Install `[agents]` to use `AgentEnvironments(provider).list()` and
`.resolve(environment_id)`. These call Agent Manager's public target API and
preserve immutable environment identity. Targets are observations, not launch
authorization, credential grants, or environment retirement leases.

Advanced consumers can launch local Codex directly from an environment UUID using Agent Manager's
public `lease_native_environment` contract. Install `[agents,native-codex]` and an
Agent Manager build exposing that API. OpenCode uses the same public contract;
other provider adapters still require explicit backend launch inputs.

```python
from conversation_manager import AgentEnvironments, Client

agents = AgentEnvironments('codex', config='/path/to/providers.toml', root='/path/to/agent-state')
targets = agents.list()
client = Client('/path/to/conversation-state')
launch = await client.launch_codex(
    targets[0]['environment_id'], agents=agents, cwd='/path/to/project',
    operation_id='codex-launch-1',
)
# Inspect launch status before using its connection reference.
if launch['status'] == 'completed':
    connection = await client.connect(launch['result']['connection_ref'])
    conversation = await connection.create_conversation(operation_id='chat-create-1')
    await conversation.send('Explain this project', operation_id='chat-send-1')
```

The equivalent CLI launch is:

```sh
conversation-manager --home /path/to/conversation-state codex launch \
  --environment-id ENVIRONMENT_UUID --agent-config /path/to/providers.toml \
  --agent-root /path/to/agent-state --cwd /path/to/project \
  --operation-id codex-launch-1 --json
```

The worker revalidates the captured environment/configuration revision and starts
the configured Codex executable on its own private Unix socket. Agent Manager
supplies the selected `CODEX_HOME` and filters competing ambient credentials.
Existing native authentication/configuration stays provider-owned; sign in using
Agent Manager beforehand. A signed-out native backend is reported as an
authentication failure. Saved model preference is used unless explicitly overridden.
Terminal launch arguments are not forwarded; use conversation settings.


## OpenCode frontend path


Select an OpenCode entry from `available_agents()` and call the same
`start_conversation()` API. Supply `settings={'model': 'provider/model'}` when
overriding its native default. OpenCode V2 `2.2.5` is exercised by the installed
executable tests using a local model fixture.

Agent Manager supplies the selected native XDG/configuration/database paths and
filters ambient credential and endpoint overrides. Conversation Manager does not
copy account credentials or edit SQLite tables. The private authenticated server
uses the selected native database, protected by an exclusive environment lease.
Active or unconfirmed Agent Manager native executions block acquiring that lease.
Unmanaged processes outside this contract are not adopted or stopped.

Within one Conversation Manager store, additional chats for the same OpenCode
environment reuse its retained worker and create distinct native session IDs.
Each chat retains its own working directory and settings. Concurrent startup can
report busy; retry the same operation ID after the first launch completes.
Different stores cannot start competing managed owners of that environment.
Configured MCP resources are shared by the private server; conflicting selections
are rejected. Native skill delivery remains unqualified for OpenCode. Model selection,
variant-backed reasoning and service-tier choices, native planning-agent selection,
and native tool permission policies are supported on version `2.2.5`. Reasoning
and speed depend on the selected model's advertised variants, just as Codex
controls depend on its native catalog. OpenCode tool permissions are not an OS
sandbox. Plan mode selects the native `plan` agent and adds session write/shell/
subagent denial rules; it does not edit user agent configuration.
A successful startup does not prove model entitlement;
that is established only by successful inference for the selected route.


## Scope and status


Conversation Manager has its own import, CLI, schemas, storage root, and
`CONVERSATION_MANAGER_HOME`. ACP and the standalone model-discovery API are excluded.

Prepared instruction, skill, and MCP delivery remains in the adapters because it
affects conversation startup. Content libraries, reconciliation, provider
installation, and chat UI organization belong to other components.

The current send API accepts text and text blocks. Images/files, history search,
message editing, and generalized branching are not implemented. Some adapters
expose additional capability-checked operations. No live provider/model acceptance
is implied by fixture tests.

See [live qualification](live-qualification.md) for prerequisites and result interpretation.
The opt-in `scripts/qualify_codex_live.py` requires `--allow-live`, an explicit
environment UUID, model, and report path; `--controls` adds cancellation and a
permission-denial probe in its disposable workspace.

`scripts/qualify_opencode_live.py --allow-live --model PROVIDER/MODEL --output REPORT`
checks a live OpenCode route in a temporary native environment. Provider quota and
model availability can prevent inference even when startup succeeds. See
[live qualification](live-qualification.md) for result interpretation.


## Resource delivery

Captured stdio MCP selections use `settings.mcp.servers` with managed server
names, command/args and workspace. Codex mapping stays inside its adapter.
`await conversation.mcp_target()` returns the retained target for
`conversation.connection.mcp_connector()` and MCP Manager's public runtime APIs.
Check inventory/reconnect capabilities: captured configuration is not a live
connection or qualified reconnect support.

Prepared instructions, skills and MCP delivery belong to adapters. Content
libraries and reconciliation remain upstream concerns. OpenCode native skill
delivery remains unqualified. See [MCP target implementation](../src/conversation_manager/mcp.py),
[skill delivery](../src/conversation_manager/skill_delivery.py) and
[resource finalization](../src/conversation_manager/resource_finalization.py).

## SDK adapters

| Protocol | Adapter | Optional extra |
| --- | --- | --- |
| `claude-agent-sdk` | `claude-sdk` | `claude-sdk` |
| `copilot-sdk` | `copilot-sdk` | `copilot-sdk` |
| `antigravity-sdk` | `antigravity-sdk` | `antigravity-sdk` |

The SDK owns native session and provider interaction; Conversation Manager owns
normalized requests, receipts and session lifecycle. Installing an extra does
not sign in or prove provider acceptance. SDK selection is explicit and lazy;
importing the package does not import every provider SDK. Credentials are
transient inputs or provider-owned bindings, not persisted launch settings.

Read [adapter selection](../src/conversation_manager/adapters/registry.py),
[SDK runtime](../src/conversation_manager/sdk_runtime.py),
[provider policy](../src/conversation_manager/provider-policy.toml), and
[dependency versions](../pyproject.toml) before extending a backend.
Shared filesystem and local-runtime utilities are declared wheel dependencies;
release installers bundle them. No `aixand-platform` package is required.

## Retain the environment lease

The worker revalidates the captured environment revision. Create native children
inside `lease.subprocess_options()` to retain the guard: an inherited file
descriptor on POSIX, or file-sharing handle and worker-owned job on Windows.
Shared Codex guards allow concurrent readers and refuse administrative writers;
OpenCode permits one managed server per environment. Stop provider helpers before
releasing the guard. Detaching does not release it. Keyed launch retries recover
the original environment rather than choosing another or spawning replacements.

New separate Codex processes use JSONL over stdio; existing socket connections
retain their transport. Shared mode uses a private WebSocket pool. A failed worker
never authorizes automatic message replay. This local native path does not
implement hosted/commercial Sign in with ChatGPT or remote execution. Account
access and service terms remain separate from successful local launch.
