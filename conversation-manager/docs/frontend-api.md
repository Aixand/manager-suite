# Frontend API

`ConversationClient` is the descriptive name for `Client`. Configure an optional
Agent Manager config/state root once at construction; the frontend does not
coordinate preparation, launch, connection, and creation itself.

| Operation | Result |
| --- | --- |
| `available_agents(providers=...)` | Agent Manager targets, serializable references, implemented startup support and verification limitations. No launch or account probe. |
| `start_conversation(agent=..., cwd=..., settings=None, operation_id=...)` | Ready `Conversation` with its durable `startup_receipt`; errors include the startup operation ID and recovery information. |
| `attach_conversation(reference)` | Bind the exact locally recorded conversation, including active work. No new backend or native conversation. |
| `conversations(detailed=False)` | Locally recorded references; detailed mode probes worker reachability and includes observed session state or an unavailable reason. |
| `operation(operation_id)` | Inspect the startup receipt, including child operation IDs and retained runtime information. |
| `stop_startup(startup_operation_id, operation_id=...)` | Explicitly terminate the startup's runtime, including failed startups that never returned a conversation. |

All operations in this table are asynchronous. Automatic startup currently supports
local POSIX Codex and OpenCode; these are the default discovery pair. Other provider
targets may be explicitly discovered, but report unsupported
automatic startup. `supported=True` means an implemented startup path, not verified
credentials, account entitlement, deployment permission, or live-provider acceptance.

## Startup recovery

A startup operation captures the exact agent reference, normalized working
directory, and settings. Changing intent under the same ID is a conflict. Persist
the selected reference and operation ID to retry independently of current aliases.
Startup claims are serialized per store/operation across frontend processes.
OpenCode also serializes worker allocation per environment. Multiple chats in the
same environment/store share its worker while retaining distinct native session
identities, settings, and working directories. Conflicting server-wide MCP settings
are rejected. A busy owner allocation can be retried using the same operation ID.

Internal deterministic operation IDs track native launch and conversation creation.
They are included in recovery information before dispatch. Launch inspection takes
precedence over resolving the agent again. Creation dispatch is checkpointed before
sending; if delivery is unknown, an absent completion does not authorize another
creation. An observed completed creation can be recovered after a cancelled wait
or an interrupted parent checkpoint. A concurrent startup reports busy, allowing
the caller to inspect/retry later.

Successful startup receipts stay completed even if their worker later stops.
Retrying then reports that the original conversation is unavailable and never
starts a replacement. Explicit restoration onto a replacement runtime remains an
advanced operation outside this frontend startup API.

Failed creation retains the runtime and child operation IDs for inspection or
explicit `stop_startup`. Cleanup can recover a runtime from its launch receipt if
the parent runtime checkpoint was interrupted. Cleanup stops all work on that
runtime; it is not a frontend detach operation. No automatic destructive cleanup
is performed after uncertain effects.
For shared OpenCode workers, cleanup terminates all conversations on that worker;
use conversation closure to close one chat without stopping the shared runtime.

## Conversation use and boundaries

`Conversation` exposes messaging, events, turn results, cancellation, steering,
history, pending requests/responses, commands, configuration, and capability
inspection. Features remain backend-dependent. `disconnect()` detaches;
`reconnect()` reattaches the same connection. Neither changes agent selection or
starts another process. `close()` invokes capability-checked native conversation
closure, distinct from process termination.

`await conversation.list_slash_commands(available_only=False)` exposes normalized
command names/descriptions, argument hints, execution and setting mappings,
unavailable reasons, and catalog provenance. Use `available_only=True` for supported
actions and the `executable` flag for actual command invocations. OpenCode discovery
currently covers registered native callbacks only, not its full TUI/template
inventory. Registered `/review` is executable on qualified OpenCode `2.2.5`;
other callbacks remain unqualified. See [slash-command SDK](slash-commands.md).

The frontend can separately use Agent Manager for administration, authentication,
model discovery, and native TUI execution through Undercurrent. Terminal executions
and managed chat conversations remain distinct. This API neither proxies terminal
operations nor promises switching between a TUI and chat on the same process.

## Session controls and queues

Use `conversation.configuration()` for currently advertised setting IDs, values,
and choices. `set_config(id, value, operation_id=...)` updates the existing session
when idle. Refresh configuration after each confirmed change: choosing a model
can alter reasoning and speed choices. Map slash commands using their `setting_id`;
controls derived from configuration are frontend affordances, not new native
provider commands. Never send an unsupported slash command as a model prompt.

| Conversation operation | Behavior |
| --- | --- |
| `queue_state()` | Current worker queue, pause state, per-mode support and delivery records. |
| `enqueue(text, mode='after_turn' or 'next_tool_completion', expected_turn_id=..., operation_id=...)` | Persist against an exact active turn. Enqueue confirmation is distinct from delivery. |
| `edit_queued(id, text, operation_id=...)`, `remove_queued(id, operation_id=...)` | Change only messages that have not started dispatch. |
| `pause_queue(operation_id=...)`, `resume_queue(operation_id=...)` | Explicit delivery control. Resume rejects unresolved input requests and interrupted dispatches. |
| `inspect_queued(id)` | Read matching retained receipt/terminal outcome; works while detached or after the worker stops. Never dispatches. |
| `reconcile_queued(id, operation_id=...)` | Record a confirmed matching handoff/failure without dispatch. Remaining work stays paused. |

`client.recorded_queue(reference)` returns a **last-recorded** snapshot without
connecting. It does not prove reachability or permit delivery. The equivalent
read-only inspection is `client.inspect_queued(reference, id)`.

Queue snapshots use `conversation-manager/message-queue-1`; delivery evidence uses
`conversation-manager/queued-delivery-1`. Inspect `modes` on a live snapshot or
`queue_modes` on capabilities. The current adapters advertise `after_turn` and, where steering is supported,
`next_tool_completion`. The latter captures the currently running tools, waits
for their observed completion and steers into the same turn. It requires
`running_tools` to be nonempty at admission. There is no pause-before-continuation
guarantee; `after_tool` is unsupported for that stricter guarantee. See [queue semantics and recovery](message-queues.md).

Consumers distinguish three outcomes: a mutation receipt (`accepted`/`completed`
or failure/uncertainty), normalized events as work proceeds, and canonical
`turn_result(turn_id)` after observed completion. `queue.updated` and
`message.input` notify queue changes and delivered inputs. After-turn FIFO messages create separate turns. Tool-completion guidance belongs
to the original turn and is rendered as an additional input, not a replacement
for its initial user message. A turn completing cannot confirm an uncertain
steering delivery. Stream cursors and history cursors are separate; preserve sequence
order when mixing manual and queued inputs. Retained history is manager-observed,
not a promise of complete native history.

Persist operation IDs before mutations and reuse the same ID with the same intent
on a retry. Unknown delivery must remain visible; do not retry with a new ID,
create a replacement worker/session, or resume pending work automatically.
Reconciliation checks the immutable send intent, including exact session and
content, before trusting a receipt. Missing/conflicting/uncertain evidence leaves
an interrupted delivery blocked. A stopped worker's queue is inspected read-only;
reconciliation requires the original worker to be available.

The maintained [reference chat example](../examples/reference_chat/README.md) exercises
these operations with isolated offline fixtures or explicitly selected live agents.
