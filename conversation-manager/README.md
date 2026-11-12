# Conversation Manager

Backend conversations for chat interfaces: messages, streaming, tool activity,
approvals, slash commands, history, cancellation, and reconnecting to retained work.

Python 3.11–3.14. Managed Codex uses native processes on Linux, macOS, and Windows.
Managed OpenCode uses native processes on Linux, macOS, and Windows.
Provider features vary; inspect the connected backend's capabilities.

```sh
python -m pip install '.[agents,native-codex]'
conversation-manager demo --fixture --json
python -m unittest discover -s tests -v
```

The demo uses an explicitly injected SDK fixture in a real local worker. It needs
no provider credentials or ACP and demonstrates detaching, reattaching, replay,
and explicit worker termination.

## Install the CLI

As an alternative to downloading a release archive, use the hosted beta installer
on Linux/macOS:

```sh
curl -fsSL https://installer.aixand.org/manager/conversation | sh
```

It bundles Python and dependencies, asks for confirmation, and keeps software
separate from existing manager data. See
[hosted installation](../docs/release-installation.md#hosted-installer-alternative)
for supported targets, Windows PowerShell, script review, and custom stores.
Python API consumers install the package in their own application's environment.

## Python interface

```python
from conversation_manager import ConversationClient

client = ConversationClient(agent_config='/path/to/providers.toml', agent_root='/path/to/agent-state')
agents = await client.available_agents()
selected = next(item for item in agents if item['conversation']['supported'])
conversation = await client.start_conversation(
    agent=selected['reference'], cwd='/path/to/project', operation_id='start-chat-1',
)
try:
    receipt = await conversation.send("Hello", operation_id="send-1")
    async for event in conversation.events():
        if event["kind"] == "turn.completed":
            break
    history = await conversation.history()
    commands = await conversation.list_slash_commands()
finally:
    await conversation.disconnect()
```

`ConversationClient` and `Client` are names for the same implementation. The normal
frontend calls `available_agents`, `start_conversation`, and `attach_conversation`;
it need not construct Agent Manager helpers, launch descriptors, or connections.
Discovery reports implemented startup support, not account entitlement or verified
sign-in. The initial frontend providers are local Codex and OpenCode;
`available_agents()` discovers those two by default.

`native_startup_support(provider)` reports the implemented platform and transport.
It does not check installation, sign-in, or model access. Suite uses this report
to select chat support instead of making its own operating system check.

`start_conversation` prepares the selected environment, launches Codex, connects,
and creates a conversation under one operation ID. The returned conversation has
a durable `startup_receipt`. Retry the exact reference, working directory, settings,
and operation ID to recover that startup. Changing intent is a conflict; an unknown
creation outcome is never resent. Failures raise `ConversationError` with the
startup operation ID and recovery information. Inspect `client.operation(id)`;
`client.stop_startup(id, operation_id='cleanup-1')` explicitly terminates a retained
startup runtime even if conversation creation failed. A stopped startup is never
silently replaced.
Cleanup stops that runtime and all work on it. OpenCode chats in the same
environment and store share a runtime, so cleanup stops all of those chats.
Use conversation closure to close an individual chat. Normal frontend
disconnection leaves the runtime running.

Sending returns a receipt: acceptance does not establish turn completion. Inspect
the exact turn result or completion events. Conversation closure, cancellation,
frontend disconnection, and worker termination are distinct operations.

`Client.conversations()` lists locally recorded references, including closed or
unavailable conversations; it does not discover all provider-native history.
Pass `detailed=True` to include observed availability; this probes retained workers
and never starts replacements. Availability describes worker reachability; inspect
session state and capabilities before submitting work. `Conversation.reference` is serializable and
retains native identity. `await client.attach_conversation(saved_reference)` binds
it even while work is running. `await conversation.reconnect()` reattaches an
existing frontend handle. Lower-level `connection.load_conversation` invokes the
backend's load operation and may require an idle conversation.

Frontend CLI commands use the same API:

```sh
conversation-manager agents --agent-config /path/to/providers.toml --agent-root /path/to/agent-state --json
# Save the selected entry's reference object as agent-reference.json.
conversation-manager conversation start --agent @agent-reference.json --cwd /path/to/project --operation-id start-chat-1 --json
conversation-manager conversation list --detailed --json
conversation-manager conversation stop-startup --startup-operation-id start-chat-1 --operation-id stop-chat-1 --json
```

Advanced integrations can use `PreparedLaunch`, `StandaloneLauncher`,
`WorkerLauncher`, and the lower-level connection/session APIs. Launch descriptors
use explicit protocol, transport, argv, working directory, settings, and transient
environment inputs. Supported paths are Codex App Server, native OpenCode, and
Claude, Copilot, and Antigravity SDKs. Optional dependency groups install SDKs;
their presence alone does not establish authenticated provider acceptance.

## Agent Manager boundary

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

## Editable configuration and default layout

Conversation Manager uses the shared manager-directory convention:

```text
~/.local/share/aixand/manager/conversation/
├── config.toml
└── state/              # Managed SQLite history, receipts and runtime references
```

Initialize and inspect it without starting a worker or creating a database:

```sh
conversation-manager config init
conversation-manager config path
conversation-manager config show
# Select another config explicitly:
conversation-manager --config /path/to/config.toml config show
```

`config init` creates a private editable template and never overwrites an existing
file. Reading defaults does not create files. Missing default configuration uses
built-ins; an explicitly selected missing or malformed config reports an error.
The initial template keeps standalone ownership and leaves optional attributes
commented out:

```toml
[storage]
home = "~/.local/share/aixand/manager/conversation/state"

[runtime]
kind = "standalone" # Or "undercurrent"

[runtime.undercurrent]
home = "~/.aixand/undercurrent"
# bridge = "/absolute/path/to/uc-python-bridge"

[process]
name_prefix = "conversation"

[process.labels]
app = "conversation-manager"

[process.metadata]
purpose = "development"
```

Use `Client(config='/path/to/config.toml')` or CLI `--config` to select the file.
Otherwise `CONVERSATION_MANAGER_CONFIG` selects it, followed by the default path.
Config-file paths expand `~`; relative paths are resolved against the directory
containing that config file. An alternate config without `[storage].home` keeps
the standard state default. An explicit SDK/CLI path resolves against the caller's
working directory as before.

Precedence for state/runtime settings is explicit SDK arguments or CLI flags,
then documented environment overrides, then file defaults, then built-ins:

- State: `Client(home=...)` / `--home`, `CONVERSATION_MANAGER_HOME`,
  `[storage].home`, then `~/.local/share/aixand/manager/conversation/state`.
- Runtime: `runtime=...` / `--runtime`, `CONVERSATION_MANAGER_RUNTIME`,
  `[runtime].kind`, then `standalone`.
- Daemon options merge per field. `runtime_options` / `--runtime-options` override
  `CONVERSATION_MANAGER_UNDERCURRENT_HOME` and
  `CONVERSATION_MANAGER_UNDERCURRENT_BRIDGE`, then the ordinary `UNDERCURRENT_HOME`
  and `UNDERCURRENT_PYTHON_BRIDGE`, then `[runtime.undercurrent]` defaults. Ordinary
  Undercurrent SDK defaults fill remaining fields. Daemon options in the file are
  dormant when standalone is selected.
- Process labels/metadata merge with per-launch `process` / `--process` attributes.
  Explicit keys win. A configured name prefix generates a stable per-start worker
  name; an explicit name wins. Config does not accept credentials or runtime
  environment-variable payloads.

`config show` reports defaults resolved against its config, flags and environment,
without connecting to a daemon. A client snapshots defaults when constructed;
create a new client after editing the file. Config changes apply to new worker
starts and never rewrite recorded runtime references or existing conversations.
Shared OpenCode chats reuse their worker's name and attributes; only explicit
per-start constraints require a matching shared worker.

The previous XDG-based default store is **not moved or automatically adopted**.
To access it, use `--home /path/to/old/store` or set `[storage].home` to that exact
path. Initialization does not modify existing data or switch active workers.
This convention is introduced in Conversation Manager first; other managers'
paths remain unchanged until their own explicit migration.

## Worker process ownership

The default `runtime="standalone"` starts a detached local worker. Install
`.[agents,native-codex,undercurrent]` to optionally let an existing Undercurrent
daemon own that worker and its provider-server process tree:

```python
client = ConversationClient(
    '/path/to/conversation-state',
    runtime='undercurrent',
    runtime_options={'home': '/path/to/undercurrent',
                     'bridge': '/absolute/path/to/uc-python-bridge'},
)
conversation = await client.start_conversation(
    agent=selected['reference'], cwd='/path/to/project', operation_id='chat-1',
    process={'name': 'Project assistant',
             'labels': {'project': 'example'},
             'metadata': {'purpose': 'development'}},
)
runtime = conversation.reference['recovery']['connection_ref']['runtime_ref']
observed = await client.inspect_runtime(runtime)
await conversation.disconnect()  # Detach; the daemon session remains running.
```

The daemon and a matching bridge must already be installed and running; the SDK
never starts a daemon or falls back to another owner. Initial support is local
POSIX, matching the native frontend startup paths. An Undercurrent native/PTY
session supervises the worker; conversation messages still travel through private
IPC rather than terminal input/output. The worker continues to start and manage
the provider server. It is one named daemon session per worker, not a separately
named daemon session per child process.

`process` accepts a display `name`, filterable string `labels`, and private string
`metadata`. Internal `cm.` attributes are reserved. Keep credentials and prompt
contents out of process attributes. Provider, connection and environment identity
are supplied as internal markers. Names may be changed through Undercurrent's
SDK; subsequent inspection and stop use the saved session ID. In standalone mode,
process attributes are retained in the local launch policy without a daemon listing.

An owner reservation and fingerprint are recorded before dispatch. After a lost
reply the original daemon session is reconciled using its saved ID or verified
markers; an empty lookup is not permission to launch again. Startup and cleanup
receipts retain the owner even if a frontend exits during bootstrap. Reconnecting
or using a new default client does not change an existing runtime's owner:

```python
reopened = ConversationClient('/path/to/conversation-state')
await reopened.inspect_runtime(runtime)
await reopened.terminate_runtime(runtime, operation_id='stop-worker-1')
# Or use stop_startup('chat-1', operation_id='stop-startup-1').
```

Stopping is separate from conversation closure. Termination first returns a
receipt; inspect until a terminal lifecycle is observed. Stopped workers are
never automatically replaced. Daemon restart recovery observes its retained
session record; it does not promise to resurrect a terminated conversation.

OpenCode chats in one store/environment share one worker and one daemon session.
Additional chats reuse its process attributes; an explicitly conflicting runtime
or process configuration is rejected. Stopping the shared worker affects all of
its chats. Explicit provider restoration checks the previous recorded owner
before authorizing replacement.

The CLI uses the same runtime selection:

```sh
conversation-manager --home /path/to/conversation-state \
  --runtime undercurrent \
  --runtime-options '{"home":"/path/to/undercurrent","bridge":"/absolute/path/to/uc-python-bridge"}' \
  conversation start --agent @agent-reference.json --cwd /path/to/project \
  --operation-id chat-1 --process '{"name":"Project assistant","labels":{"project":"example"}}' --json
```

Low-level `launch` and `codex launch` also accept `--process`. The explicit fixture
`demo` remains standalone and rejects a conflicting runtime option. Existing explicitly
injected launchers remain supported and own their configuration. They cannot be
combined with built-in process attributes. `WorkerLauncher` owners may optionally
implement synchronous `prepare(command, cwd=..., operation_id=...)` to reserve a
stable runtime identity before process IO; its returned identity must match
`start`. The base start/inspect/stop contract remains supported.

## Slash-command discovery SDK

```python
catalog = await conversation.list_slash_commands()
supported = await conversation.list_slash_commands(available_only=True)
for command in catalog['commands']:
    print(command['slash'], command['description'], command['execution'])
```

Entries include argument hints when known, execution support, idle requirements,
setting IDs, and unavailable reasons. `execution='native'` can use
`conversation.command(name, ...)`; `execution='setting'` is a settings control
handled with `set_config(setting_id, value, ...)`. Codex/OpenCode common setting
aliases also work through `command(name, arguments, ...)`; the worker routes them
to the same native setting operation. Catalog `argument_options` supplies actual
choices for completion after a space. Discovery does not execute a
command or invoke a model. Raw adapter catalogs remain available via `commands()`.

Codex uses its reviewed CLI inventory with supported session mappings. OpenCode
queries the live registered-callback catalog for the conversation's working
directory. That API may return no entries and does not claim the complete TUI or
prompt-template command inventory. OpenCode initializes its native agents before querying registered callbacks.
On qualified version `2.2.5`, advertised `/review` and `/init` callbacks are
executable. The chat adapter also maps `/compact`, `/rename`, `/diff`, `/status`,
`/debug-config`, `/usage` and `/mcp` through native session APIs. Other callbacks
remain unsupported until their lifecycle semantics are qualified. `complete=False` explicitly records
catalog limitations. See [the SDK contract](docs/slash-commands.md).

An environment lease is retained until the worker stops, protecting against
administrative environment changes and retirement. Create the native child inside
`lease.subprocess_options()` to retain its guard. POSIX uses an inherited file
descriptor. Windows uses an inherited file-sharing handle and a worker-owned job.
Shared Codex guards permit concurrent readers and refuse administrative writers.
OpenCode guards permit one server per environment. The process owner stops
provider helpers before it releases the guard. Detaching does not release the
lease or stop the provider. Terminate
explicitly using the recorded runtime reference. Keyed retries recover recorded
launch intent without selecting a different environment or spawning a replacement.

New managed Codex processes use JSONL over stdio. Existing socket connections
retain their transport. Closing a frontend leaves the worker running. A failed
worker does not cause an automatic replacement or message replay.

This path targets local native use. It does not implement hosted/commercial Sign
in with ChatGPT, remote execution, or automatic cross-environment history transfer.
Provider account access and applicable service terms remain separate from a
successful local launch.

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

See [live qualification](docs/live-qualification.md) for prerequisites and result interpretation.
The opt-in `scripts/qualify_codex_live.py` requires `--allow-live`, an explicit
environment UUID, model, and report path; `--controls` adds cancellation and a
permission-denial probe in its disposable workspace.

`scripts/qualify_opencode_live.py --allow-live --model PROVIDER/MODEL --output REPORT`
checks a live OpenCode route in a temporary native environment. Provider quota and
model availability can prevent inference even when startup succeeds. See
[live qualification](docs/live-qualification.md) for result interpretation.

## Codex and OpenCode controls

Both initial providers share `configuration()`, `set_config()`, command discovery,
argument choices, native command execution and durable operation receipts. Common
controls are model, reasoning effort (`effort`), service tier (`serviceTier`),
conversation mode (`mode`) and permissions. OpenCode also exposes its native
`variant` selector. Values remain provider/model-specific and are never invented.

```python
options = await conversation.configuration()
commands = await conversation.list_slash_commands(available_only=True)
await conversation.command('reasoning', 'high', operation_id='reasoning-1')
await conversation.command('plan', 'on', operation_id='planning-1')
# Select actual advertised permission values for the connected provider.
await conversation.set_config('permissions', selected_permission, operation_id='permissions-1')
```

`/model`, `/reasoning`, `/effort`, `/permissions`, `/mode`, `/plan`, `/speed` and
`/fast` use the same setting-command path. With no argument, ordinary setting
commands return their choices; `/plan` selects planning mode and `/fast` selects
an advertised fast/priority tier. `off` selects default mode/tier and `status`
returns the current option without changing it. Unsupported speed/effort choices
are rejected before mutation. Changing reasoning preserves an advertised speed
combination and vice versa. Model changes clear incompatible variants rather than
applying an old model's options to a different model.

Settings and setting commands require an idle session. The worker excludes new
sends/other settings mutations during an update, checks native readback, and
pauses pending queue delivery if the update is unconfirmed. Retrying the same
operation inspects its original receipt instead of applying the setting again.
Normal settings changes and direct inspection commands do not invoke a model.
Review, initialization and compaction may invoke the model and retain turn
completion/cancellation semantics. Provider-native and terminal-only commands
outside these chat mappings still report explicit support information; this is
not an emulation of every TUI action or a promise that every model has a fast tier.

## Message queues

`Conversation.enqueue`, `queue_state`, `edit_queued`, `remove_queued`,
`pause_queue`, and `resume_queue` manage a worker-owned persistent FIFO queue.
After-turn delivery survives frontend disconnection and pauses on cancellation,
failure, or agent input requests. Use `next_tool_completion` to steer into the active turn after all tools
running at enqueue time are observed finished. If that turn ends first, the
message stays retained. This timing does not guarantee a pause before continuation;
exact native tool-boundary delivery remains unsupported. See [message queue semantics](docs/message-queues.md).

A maintained [reference chat frontend](examples/reference_chat/README.md) includes
offline browser checks, optional live agent discovery, settings, queues and recovery.


Captured stdio MCP selections can use `settings.mcp.servers` with managed server
names, command/args and workspace. Codex mapping stays inside its adapter. A
conversation's `mcp_target()` returns its exact retained target for use with
`conversation.connection.mcp_connector()` and MCP Manager's public runtime APIs.
Check the connector's inventory/reconnect capabilities; a captured configuration
does not establish a live connection or qualified reconnect support.

Shared utilities are packaged from `../../aixand-libraries/filesystem` and
`../../aixand-libraries/local-runtime` in the sibling library checkout. Installed wheels declare these
package dependencies; release installers bundle them. No `aixand-platform`
package or external platform checkout is required.

## Local developer commands

Inside the manager-suites source checkout, run from this directory with
Python 3.11+ and uv:

```sh
./dev.sh develop --local
./dev.sh check
./dev.sh test
./dev.sh build
```

Use `.\dev.ps1` on Windows. Each package owns its development environment and
helper; these commands do not invoke suite-wide checks. See the
[development guide](docs/development.md) for wheel-based setup without sibling
sources, lock updates, build outputs, and the separate suite release lifecycle.

After a machine restart, use `resume_conversation` to open a saved managed Codex
or OpenCode session. Save its returned reference for later use:

```python
conversation = await client.resume_conversation(
    saved_reference, operation_id='resume:' + saved_reference['session_id'],
)
saved_reference = conversation.reference
```

This call inspects the recorded owner before it starts a replacement worker.
A live owner keeps its worker. An unknown owner blocks restore. A stopped owner
permits restore of the same native session in the same native home. The call
keeps saved history and confirmed settings. It does not resend previous messages,
commands, or queued work. A repeated operation uses its recorded restore result.
`attach_conversation` still attaches only. It reports a stopped built-in owner
without waiting for an IPC connection timeout.

### Default storage location

The default product directory is `${XDG_DATA_HOME:-~/.local/share}/aixand/manager/conversation`.
Set `AIXAND_MANAGER_ROOT=/custom/managers` to use `/custom/managers/conversation`
without changing the data location of unrelated applications. Unset, empty, or
relative `XDG_DATA_HOME` values fall back to `~/.local/share`.
Explicit CLI/SDK paths and product-specific environment overrides take precedence;
these paths are used directly. Existing configured storage paths remain supported.
Agent and Conversation config overrides remain separate from their state-home overrides.
Default path resolution creates no directories and does not move or adopt old stores.
To keep using an existing store, supply its original path explicitly. Native provider
homes connected by the user and operating-system temporary files retain their locations.
