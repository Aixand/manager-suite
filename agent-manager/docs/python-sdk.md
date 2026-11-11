# Agent Manager Python SDK

See the [v1 contract](sdk-contract-v1.md) for public signatures, return values,
ownership, errors, retry guarantees, and packaged consumer qualification.

Agent Manager manages native provider profiles, environments, configuration and
execution. Supported native providers are Codex, Claude Code, Copilot,
OpenCode and Antigravity, subject to their declared capabilities.

## Profile and configuration operations

`Manager` is synchronous and does not require Undercurrent:

```python
from agent_manager import Manager

manager = Manager('codex')
manager.new_profile('main')
manager.set_default('main')
manager.login('main')
print(manager.readiness('main'))
```

It also exposes environment and alias management, native instructions, saved launch
arguments, models, integrations, budgets, readiness and provider installation planning.
It has no public `run` or `invoke` method. Direct terminal execution belongs to the
CLI; SDK launches and continuation belong to `Client`. See [configuration](configuration.md),
[profile identities](profiles-and-environments.md), and
[native continuation](file-session-continuation.md).

`agent_targets()` and `resolve_agent_target(environment_id)` expose stable native
environments for independent tools. Profile names are aliases; use environment
identities when an integration must survive alias changes.

## Managed execution

Install `aixand-agent-manager[sdk]` or `aixand-agent-manager[undercurrent]` and provide the
Undercurrent binaries and daemon. Both extras install only `aixand-undercurrent-sdk`.
The SDK uses native providers through Undercurrent.
Managed Codex launches use `--no-daemon` so their native backend lifetime belongs
to the managed process; remote attachment is unsupported. Direct native CLI
launches keep the provider's normal daemon behavior.
Undercurrent is mandatory for SDK execution. `runtime = "direct"` is a CLI setting
and does not change `Client` execution. There is no SDK direct/subprocess mode.

```python
import asyncio
import undercurrent
from agent_manager import Client

async def main():
    async with undercurrent.Client() as runtime:
        async with Client('codex', runtime=runtime) as agents:
            run = await agents.spawn(profile='main', cwd='.')
            observation = await agents.execution_status(run.run_id)
            print(observation['state'])
            # Process operations belong to Undercurrent.
            await runtime.stop(run.session_id)

asyncio.run(main())
```

`Client` provides `providers()` inventory, async profile/configuration operations,
`spawn`, `get`, `runs`,
`start`, `metadata`, `execution_status`, `sessions`, `continue_session`, and reconciliation.
Pass an existing runtime to borrow it; closing the Client does not close a borrowed
runtime. A Client-created runtime connection is closed by the Client. Closing either
connection does not terminate the provider process.

`Agent` contains the Agent Manager run ID, provider, profile/environment identities,
native home, Undercurrent session ID, optional native thread ID, launch kind and
labels. It is an execution identity with thin Undercurrent handle operations, not a
conversation-protocol client.
Retain `run_id` to recover the same managed execution with `get(run_id)`.

`spawn(idempotency_key=UUID, ...)` records its intent before dispatch. Reusing the
same key with changed arguments fails. Cancellation or a lost reply does not imply
that execution stopped; inspect/reconcile the retained run rather than launching
another process. Unknown runtime observations retain execution leases.

`execution_status` is a read-only observation. Reconciliation may finalize a run
only after termination or absence is established by its original runtime.
Native profile/home guards and session leases remain in force for all launches.

## Native continuation

```python
sessions = await agents.sessions()
run = await agents.continue_session(sessions[0]['ref'], profile='main')
```

Continuation launches a native terminal in the saved workspace. Profile selection
is noninteractive in the SDK. Cross-profile behavior depends on the provider: some
retain the original history store, while OpenCode uses native transcript copies.
Codex cross-profile continuation uses Agent Manager's own private RPC client and
requires Linux, bubblewrap/user namespaces, and a qualified native Codex version.
For qualified Linux Codex versions with native writer-lock metadata, same-home
continuation checks the selected conversation's exact native lock instead of
blocking solely because unrelated terminals have unknown thread identities.
Known same-conversation writers still block; absent qualification keeps the
conservative guard. Cross-home admission is unchanged.
Cross-profile Codex continuation preserves explicit saved bypass flags through
native sandbox/approval configuration. Brief coordination contention retries only
read-only writer verification; model/native requests are not replayed.
Native history is distinct from programmatic conversation messaging.

Codex `spawn(thread_id=UUID)` selects a saved native thread within the selected
profile. `continue_session` handles structured cross-profile selection. Saved
arguments, a default model, and supported `native_config` settings apply to native
terminal launches. Invalid continuation options fail before history preparation.

## Context and administration

`Context`, `application_context` and `project_context` provide explicit labels and
private metadata. `with_context` creates a borrowed Client view with independent
context defaults. Process labels are query-visible; private metadata requires an
explicit read. See [launch context](launch-context.md).

The optional `aixand-agent-manager[mcp]` extra exposes Agent Manager administration through
`agent-manager-mcp`. It does not manage MCP resources for native agents.

## Scope

There are no backend launch/connect APIs, app-server client/controller interfaces,
provider-SDK installers, conversation streaming/approval APIs, or managed backend
session recovery. The `backend` and `provider-sdk` CLI commands are removed.
Native budget/model probes may start a temporary provider-owned server; these do
serve bounded observations rather than conversation messaging.

## Execution contract, version 1

`SDK_CONTRACT_VERSION` is `1`. Public value types include `ExecutionReference`,
`ExecutionState`, `ExecutionUpdate`, `SelectionDecision`, `BudgetObservation`, and
`SavedSession`. Budget and session observations retain their existing dictionary
formats; their public type declarations describe the common fields. Execution,
state, and selection serialization have explicit versioned schemas.

An `Agent` returned by `spawn`, `start`, `get`, or `continue_session` is also a bound
execution handle. Its dataclass identity remains serializable without retaining the
Client or its runtime connection. Handle methods validate the recorded run and exact
Undercurrent home/session before invoking the runtime. Closing the Client detaches;
it does not stop the agent. Recover through a new connected Client after detaching.

```python
import json
import uuid
from contextlib import aclosing
from agent_manager import Client

async with Client('codex') as agents:
    agent = await agents.start(cwd='/project', idempotency_key=str(uuid.uuid4()))
    print(agent.selection.reason, agent.selection.remaining_percent)
    print((await agent.inspect()).to_dict())
    await agent.wait(lifecycle='running', interaction='available', timeout=30)
    receipt = await agent.send_input('Review these changes', delivery_id='review-1')
    screen = await agent.read_screen()       # bytes, including terminal escapes
    page = await agent.read_output(from_offset=0, max_bytes=65536)
    offset = page.from_offset + len(page.data)
    saved = json.dumps(agent.reference.to_dict())

async with Client('codex') as agents:
    recovered = await agents.get(json.loads(saved))
    async with aclosing(recovered.watch()) as updates:
        update = await anext(updates)
        print(update.state.lifecycle, update.resynchronized)
    # Stop is explicit. A request may return while termination is still pending.
    await recovered.stop(graceful_only=True)
    await recovered.wait(lifecycle=('stopped', 'exited', 'failed'), timeout=30)
```

References contain provider, local registry path, run ID, canonical Undercurrent
home, and exact runtime session ID. They are local references, not credentials or
portable remote connection descriptors. Reconnect with the same registry/provider
and original runtime home. `get(reference)` rejects mismatched identities and never
launches a replacement. Profile renames do not change the reference.

| Handle operation | Result |
| --- | --- |
| `inspect()` | `ExecutionState`, with separate lifecycle, execution, interaction, constraint, health and revision |
| `send_input(text, ...)`, `send_key(key, ...)` | Original Undercurrent input receipt |
| `read_screen()` | Raw terminal bytes |
| `read_output(from_offset=..., max_bytes=...)` | Original Undercurrent output page, including producer offsets |
| `wait(timeout=..., after_revision=..., **conditions)` | `ExecutionState` satisfying explicit native conditions |
| `watch()` | `ExecutionUpdate` stream preserving resynchronization markers |
| `events(after_sequence=...)` | Original Undercurrent event/gap envelopes |
| `stop(force=False, graceful_only=False, expected_revision=None)` | Observed `ExecutionState`; does not independently release execution leases |

Use `contextlib.aclosing` when leaving streams early. Resume output at the end of
the returned bytes (`from_offset + len(data)`), not the producer's `next_offset`,
which may point beyond this page. Handle Undercurrent output gaps and event gaps
explicitly. Advanced operations remain available on `client.runtime` using
`agent.session_id`; `agent.runtime_ref` also describes its runtime home and ID.

Startup returns after runtime acknowledgement, not provider readiness. Applications
can wait for qualified observation conditions before sending input; providers
without readiness detection may need application-specific handling.

State axes retain Undercurrent's values. Lifecycle includes `running`, `stopping`,
`stopped`, `exited`, and `failed`. Execution is `active`, `quiescent`, or `unknown`;
interaction is `none`, `available`, or `requested`. Neither an available terminal
nor a quiescent process proves task success. `observed_at` is the local receipt time
of the observation, not the provider's completion timestamp.

Input receipts confirm terminal delivery only. Exceptions and uncertain outcomes
are not retried by Agent Manager. Supply a stable `delivery_id` when the application
needs to reconcile duplicate input using Undercurrent's semantics. `wait` requires
at least one explicit condition; it does not infer a completed agent task.

## Automatic profile selection

`start()` reuses CLI startup policy within the selected provider. Where qualified
allowance is available, it selects the highest usable limiting-window percentage.
Aliases share an environment observation. Providers without comparable ranking use
their declared default-profile policy, with the reason retained in the decision.
Account percentages are not compared across providers or incompatible model routes.

Selection runs in the requested workspace and is revalidated under the execution
guards before dispatch. It does not reserve remote quota. The resulting handle's
`selection` includes the environment revision, chosen model, reason, percentage,
and public budget evidence. Explicit `spawn(profile=...)` does not query budgets.

The default `fallback='error'` raises `SelectionUnavailable` when policy cannot
select a usable profile. `fallback='default'` explicitly permits launching the
configured default without budget qualification, including when quota cannot be
established; the returned reason records that choice. Neither mode runs onboarding,
creates profiles, changes the configured default, or silently chooses an arbitrary
account. Conflicting explicit/native model settings are rejected.

A UUID `idempotency_key` retains the selection and launch intent before dispatch.
Retries recover that execution without reranking, even after allowance changes or
alias renames. Changed launch arguments conflict. If the accepted selection becomes
invalid before any dispatch, a new key is needed to request a fresh decision. For
uncertain dispatch, recover the original key/run before considering a replacement.
The private automatic-selection journal stores an intent digest and public budget
metadata, not caller environment values.

## Folder discovery and automatic continuation

```python
sessions = await agents.sessions(cwd='/project')  # exact canonical workspace
nested = await agents.sessions(cwd='/project', recursive=True)
cached = await agents.sessions(cwd='/project', refresh=False)
all_history = await agents.sessions(include_archived=True, include_children=True)
agent = await agents.continue_session(
    sessions[0]['ref'], auto=True, idempotency_key=str(uuid.uuid4())
)
```

Discovery combines retained references and provider-native metadata from registered
environments. A saved conversation is separate from a running execution. Discovery
is bounded by each provider's history adapter; it does not start a conversation.
Recursive discovery requires `cwd` and includes descendant canonical workspaces.

Archived sessions and child sessions are excluded by default. Use
`include_archived=True` or `include_children=True` to include them. These flags also
apply to cached records. Archive records include `archived`. Child records include
`parent_id`. Use `ref` as the session identity. Use `native_id` only for display or
provider operations. Profile aliases can change without a change to `ref`.

`auto=True` restricts selection to environments eligible for this source session's
native handoff. The source workspace remains pinned. It is mutually exclusive with
an explicit `profile`, supports the same explicit fallback policy as `start`, and
retains keyed decisions for recovery without repeating native history preparation.
Omitting both `auto` and `profile` retains the source environment selection behavior.

The startup `timeout` bounds worker acknowledgement (including keyed recovery);
budget readers have their own bounded deadlines. Applications may apply a separate
`asyncio.timeout` deadline. Cancellation does not prove that dispatch had no effect;
recover the retained key/reference before starting replacement work.

## Validation and retry ordering

SDK-recognized option errors (including invalid timeouts, resume selectors, native
settings and context limits) are rejected before continuation copies or links
native history. Environment revisions are also checked before that preparation.

For automatic startup, pure input normalization and intent matching precede launch
preflight. An accepted keyed execution can be recovered after its workspace has
been removed; a fresh launch still requires an existing directory. Recovery does
not recreate that directory or start replacement work.

Explicit keyed spawning validates the canonical Undercurrent home and native
launch arguments against the retained record. Changing only bridge paths, RPC
request timeouts or frame bounds does not change the work's identity. Changing the
runtime home, native arguments or environment still fails the idempotency check.
Transport details remain recorded for observation without rewriting the accepted
launch signature.
