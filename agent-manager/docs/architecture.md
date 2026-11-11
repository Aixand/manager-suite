# Agent Manager architecture

Agent Manager owns native provider profiles, environments, installation,
configuration, account/model observations, and native execution. Undercurrent is
an optional execution runtime.

## Layout

| Module | Responsibility |
| --- | --- |
| `manager.py` | Public synchronous administration facade |
| `interfaces/cli/` | Command parsing, presentation, onboarding and private native terminal execution |
| `interfaces/sdk.py` | Async administration, automatic startup and managed native execution |
| `interfaces/contracts.py` | Versioned public SDK references, state and selection values |
| `runtime/handle.py` | Exact execution targeting and thin Undercurrent operations |
| `runtime/automatic.py` | Durable automatic selection and keyed recovery |
| `providers/` | Strict provider definitions, preferences and native release policy |
| `profiles/`, `environments/` | Aliases, immutable identities, native homes and retirement guards |
| `runtime/` | Execution records, context, process observation and leases |
| `runtime/native/` | Provider-native history, terminal continuation and CLI transport helpers |
| `runtime/continuation/` | Shared native continuation contract and coordinator |
| `budgets/`, `models/`, `integrations/` | Bounded native observations and caches |
| `storage/` | Atomic persistence, private paths and locks |

## Ownership

Environment UUIDs identify native homes and execution preferences. Profile UUIDs
identify editable aliases. Alias rename/removal does not rename native directories
or erase session provenance. Providers own authentication and credential refresh;
Agent Manager selects the native environment and filters conflicting overrides.
It does not maintain an independent credential vault.

CLI native execution is direct by default. The public Manager administration API
has no direct execution methods; private CLI dispatch lives in
`interfaces/cli/execution.py`. SDK execution always requires Undercurrent. Optional Undercurrent CLI execution and
SDK `Client.spawn` create durable run intents before dispatch. Undercurrent is the
process lifecycle authority; Agent Manager owns profile selection, home guards,
context and native-session leases. The administrative finalizer observes terminal
runtime evidence before releasing leases. Cancellation, detachment and uncertain
observations never authorize replacement execution or early lease release.

`Client` can borrow a runtime connection. Closing it does not stop native work.
Use Undercurrent for terminal attachment, process I/O and termination. Agent Manager
run IDs and Undercurrent session IDs identify different records and are not
interchangeable. Keyed retries validate the recorded launch intent before recovery.

## Continuation boundary

`runtime/continuation/contracts.py` defines native continuation identities and plans.
The coordinator captures the source, selects the destination environment, prepares
provider-specific history access, then revalidates the plan under execution guards.
CLI and SDK share this coordinator. Invalid SDK launch options are rejected before
preparing or copying history.

Native session references retain provider, native ID, history location, workspace
and origin environment identity. Cross-provider continuation is unsupported.
Codex uses the original SQLite/transcript store and writer-lock directory in an
isolated Linux mount namespace while retaining the destination's native login.
Its private `codex_wire.py` verifies the native handoff.
Claude/Copilot use their qualified original-history adapters; OpenCode uses native
transcript copies with its own recoverable copy journal. Qualification and platform
limits remain provider-specific. See [native history](file-session-continuation.md)
and [OpenCode continuation](opencode-continuation.md).

## Observations and administration

Budgets, models and native integrations use bounded provider-owned probes. Some
start temporary native app-servers or metadata servers. These are observation
mechanisms with a bounded lifetime. Observations are environment/project scoped and do not imply
that a model or provider feature is usable merely because a package is installed.

The optional management MCP interface exposes Agent Manager administration.
Prompt/skill/MCP resource delivery and Carrier orchestration belong to external
tools. Agent Manager exposes resource-neutral environment targets and native
instruction-file editing; it does not read historical resource assignments.

## Packaging and validation

The base wheel requires no other manager. `[sdk]` and `[undercurrent]` install only
`aixand-undercurrent-sdk`; `[mcp]` installs the administration transport. Provider SDK pins,
installers and backend extras are absent. Provider installation policy contains
native CLI releases only, independently of any conversation backend policy.

Run `python -m unittest discover -s tests -t .` for automated checks.
`UC_TEST_BIN=/path/to/bin` enables isolated fixture-provider Undercurrent checks in
`tests/test_sdk_live.py`. These tests require no account or model requests.
`tests/test_native_sdk_boundary.py` verifies dependency/API separation and native
SDK recovery without conversation packages. Use the
[product installer](../../docs/INSTALLATION.md) to install built releases.

Automatic startup journals retain the selected environment and an intent digest
before dispatch. Workspace context and frozen provider settings propagate into
parallel budget probes. The selected allowance/environment is checked again under
execution guards. Keyed retries recover the original run rather than reranking.
Execution handles revalidate registry, runtime home and exact session identity;
input receipts, raw output offsets, event gaps and watch resynchronization retain
Undercurrent semantics. This adds terminal control, not a conversation backend.
