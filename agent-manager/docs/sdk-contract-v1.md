# Python SDK contract v1

`from agent_manager import SDK_CONTRACT_VERSION` returns `1`. Import application
APIs from `agent_manager`; modules beneath it are implementation details. Underscore
parameters and attributes are private even when visible in a Python signature.
This contract covers local profile administration and native execution through
Undercurrent. It does not define a conversation/model API.

## Construction and ownership

```python
Client(provider='codex', *, runtime=None, config=None, root=None,
       labels=None, metadata=None)
Manager(provider, config=None, root=None)
```

`config` identifies the provider configuration file; `root` is the state root
containing provider registries, not an individual provider's native home.
`Manager` is synchronous administration and needs no Undercurrent installation.
`Client` is asynchronous and requires `aixand-agent-manager[sdk]` and an available local
Undercurrent runtime. The caller provisions the binaries and daemon.

Use `async with Client(...) as agents` to connect and reconcile retained work.
A supplied `runtime` is borrowed: closing the Client does not close it. A runtime
constructed by the Client is closed by `Client.close()` or context exit. Neither
closes the provider process. Shutdown of execution is always explicit. Managed Codex launches supply
`--no-daemon` to keep the native backend within that execution's lifetime;
remote Codex attachment is unsupported. Direct native CLI execution retains its
normal provider behavior.
`with_context(labels=..., metadata=..., inherit_labels=True,
inherit_metadata=True)` returns a borrowed Client view sharing that runtime.
`context` returns the resolved labels and private metadata.

## Execution methods

All methods below are awaited. Successful launch/recovery returns a bound `Agent`.

| Method | Arguments and behavior |
| --- | --- |
| `spawn` | Explicit profile, or configured default if omitted; launch without querying allowance. |
| `start` | Choose within this provider by its selection policy, then launch. |
| `continue_session` | Resume a saved native session; returns a new execution rather than reconnecting an existing process. |
| `get` | Accept a run ID string, `ExecutionReference`, or serialized reference dict; recover that exact execution, including retained terminated executions. Never launch a replacement. |

Public `spawn` keyword arguments:

```python
spawn(*, profile=..., args=(), cwd=None, env=None,
      detection_profile=None, detection=True, timeout=60.0, model=None,
      thread_id=None, idempotency_key=None,
      labels=None, metadata=None, inherit_labels=True, inherit_metadata=True,
      expected_revision=None, resume_id=None, native_config=None)
```

`args` is a sequence of strings; `env` is a mapping of strings to strings.
`cwd` accepts a path or string. `timeout` bounds acknowledgement waiting, not the
provider lifetime. `model` is an explicit native model override. `native_config`
is a provider-qualified mapping; validate it with `validate_native_config`.
`thread_id` is a saved Codex UUID. For general continuation prefer the structured
`sessions()[i]['ref']` and `continue_session`, rather than provider argv or raw
`resume_id`. Continuation selectors and supported native settings depend on provider
capabilities. `expected_revision` guards the captured environment configuration.

```python
start(*, args=(), cwd=None, model=None, fallback='error',
      idempotency_key=None, **options)
continue_session(session, *, profile=None, auto=False, fallback='error', **options)
```

`start` accepts the applicable public launch options above, excluding explicit
profile and resume selectors. An omitted working directory uses the current
working directory. `continue_session` accepts a saved reference or native session
ID string, an explicit profile or `auto=True`, and applicable launch options.
It chooses the saved workspace and constructs resume arguments itself; forwarded
`args`, `thread_id`, and `resume_id` are rejected. `profile` and `auto=True` conflict.
Without either, continuation resolves the session's origin profile where supported.
Native-history preparation can copy or link data according to provider policy.
Qualified Linux Codex same-home admission uses its exact native writer lock;
known same-conversation writers still block. Unqualified or unavailable lock
protocols preserve conservative admission, and cross-home rules are unchanged.
SDK-recognized invalid options and stale environment revisions fail before that
preparation; later provider/runtime failures can still occur.

`start` and automatic continuation default to `fallback='error'`.
`SelectionUnavailable` means no profile satisfied selection policy.
`fallback='default'` explicitly permits an unqualified configured default.
Neither operation creates profiles, runs onboarding, or changes defaults.
Selection is within a provider and compatible model routes, not across providers.
It does not reserve remote quota. See [selection details](python-sdk.md#automatic-profile-selection).

## Identity and observations

| Value | Stable fields / serialization |
| --- | --- |
| `Agent` | `run_id`, `provider`, `profile`, `home`, `session_id`, `thread_id`, `launch_kind`, `labels`, `environment_id`, `profile_id`, `home_id`; `reference`, `runtime_ref`, and optional `selection` properties. |
| `ExecutionReference` | `provider`, `registry_path`, `run_id`, `runtime_home`, `session_id`; `to_dict()` / `from_dict()`, schema `agent-manager/execution-ref-1`. |
| `ExecutionState` | `reference`, `lifecycle`, `execution`, `interaction`, `constraint`, `health`, `revision`, `observed_at`; `to_dict()`, schema `agent-manager/execution-state-1`, source `undercurrent`. |
| `ExecutionUpdate` | `state: ExecutionState`, `resynchronized: bool`. |
| `SelectionDecision` | `provider`, `profile`, `profile_id`, `environment_id`, `environment_revision`, `reason`, `model`, `remaining_percent`, `unlimited`, `budget`; `to_dict()` / `from_dict()`, schema `agent-manager/selection-1`. |
| `BudgetObservation` | Typed dictionary of account or connection allowance; inspect `status`, `scope`, timestamps, `buckets`, `reason` and `error` before using numbers. |
| `SavedSession` | Typed dictionary containing `ref`, `native_id`, `provider`, `cwd`, `title`, `updated` and available origin/history fields. |

Retain references via `reference.to_dict()`, not by serializing runtime/client objects.
References are local: recovery must use the same provider, registry and canonical
Undercurrent home. They are not portable remote connection descriptors. Profile
aliases can be renamed; environment and profile identities preserve targeting.
A manually reconstructed `Agent` must be recovered through `get` before control.

A saved native session describes history on disk; an execution describes a launched
process. `sessions` can find history without an active process. `runs` can list
executions without proving they are still running. Use `inspect` or
`execution_status` for observations. `ExecutionState.snapshot` exposes the original
Undercurrent snapshot; its implementation-specific fields are not Agent Manager's
serialized contract. Reference and selection deserializers require the exact
schema and fields; store their serialized output unchanged. Incompatible changes
to these shapes or execution guarantees require a new contract version. Additional
provider observation fields do not require a version change.

## Bound execution operations

| Awaited operation | Result |
| --- | --- |
| `inspect()` | `ExecutionState`. |
| `wait(timeout=300.0, after_revision=None, **conditions)` | `ExecutionState` satisfying explicit Undercurrent conditions, such as `lifecycle` or `interaction`. At least one condition is required. |
| `send_input(text, submit=True, delivery_id=None, expected_revision=None, preserve_observation=False, paste=None)` | Original Undercurrent input receipt. |
| `send_key(key, delivery_id=None)` | Original Undercurrent input receipt. |
| `read_screen()` | Terminal bytes, including escape sequences. |
| `read_output(from_offset=0, max_bytes=65536)` | Original Undercurrent output page. Advance by `page.from_offset + len(page.data)` and handle gaps. |
| `stop(force=False, graceful_only=False, expected_revision=None)` | Observed `ExecutionState`; termination may still be pending. Wait for a terminal lifecycle if needed. |

`watch()` yields `ExecutionUpdate` objects; preserve `resynchronized` markers.
`events(after_sequence=None)` yields original Undercurrent event/gap envelopes.
Use `contextlib.aclosing` when leaving either stream early.
Keyword options on handle operations are keyword-only. These operations validate
the retained execution identity before calling Undercurrent.

Lifecycle values include `running`, `stopping`, `stopped`, `exited`, `failed`.
Execution is `active`, `quiescent`, or `unknown`; interaction is `none`, `available`,
or `requested`. Startup acknowledges dispatch, not readiness. An available terminal,
quiescent process, or delivered input is not proof of task success.
`observed_at` is the local receipt time, not a provider completion timestamp.
Use native Undercurrent key names such as `enter` and `down`; handle methods do
not convert display names such as `Enter` into protocol values.
Advanced runtime operations, including stability controls, remain available through
`client.runtime`. Native Undercurrent result types and errors belong to that SDK's
contract.

## Inventory and administration

These are async `Client` operations. The corresponding synchronous administration
is available through `Manager`; method names need not be identical.
Dictionary observations can include provider-specific fields; callers should read
required fields and tolerate additional observation fields.

| Methods | Returns / purpose |
| --- | --- |
| `providers()` | Native provider inventory dictionary. |
| `profiles()`, `get_profile(profile)` | List of public profile dictionaries / one profile dictionary. |
| `environments()`, `get_environment(environment_id)` | List / one environment dictionary including revision and aliases. |
| `agent_targets()`, `resolve_agent_target(environment_id)` | Public native target descriptors for independent tools. |
| `Manager.lease_native_environment(environment_id, *, expected_revision=None)` | Synchronous context manager for a guarded local POSIX Codex/OpenCode environment; see the [native environment contract](native-environment-lease.md). |
| `budget(profile=None, refresh=False, cached=False, cwd=None, model=None)` | `BudgetObservation`. |
| `budgets(refresh=False, cached=False, cwd=None, model=None)` | One `BudgetObservation` per environment, not per alias. |
| `select_profile(model=None, args=())` | Selected profile dictionary with `selection` evidence; does not launch. |
| `sessions(cwd=None, recursive=False, refresh=True)` | `list[SavedSession]`, optionally filtered by folder. |
| `runs(include_finished=True, limit=250, labels=None)` | Bounded public execution dictionaries; no credentials, launch environment, or private metadata. |
| `execution_status(run_id)` | Dictionary with schema `agent-manager/execution-observation-1`; `state` is `active`, `terminated`, or `unknown`; `phase` is the administrative phase. |
| `metadata(run_id)` | Private execution metadata dictionary, explicitly read. |
| `threads(profile=None, labels=None, limit=250)`, `thread_metadata(profile, thread_id)` | Retained native thread context records / private metadata; not a messaging API. |
| `reconcile()` | List of retained run IDs for which administrative workers were started. |
| `create_profile`, `connect_profile`, `alias_profile`, `duplicate_profile`, `rename_profile`, `get_profile` | Profile metadata dictionaries. |
| `delete_profile`, `forget_environment`, `purge_environment` | Removal result dictionaries; ownership, history and active-execution guards apply. |
| `set_default_profile` | `None`; records the preferred profile alias. |
| `set_model` | Integer `0` on success. |
| `login`, `logout` | Native CLI integer exit codes; login can require an interactive terminal. |
| `login_status` | Native authentication observation dictionary with `state` and `verification`. |
| `identity_snapshot()` | Public identity snapshot dictionary. |
| `configuration`, `refresh_configuration`, `launch_arguments`, `set_launch_arguments` | Configuration / saved-argument snapshot dictionaries, as described in [configuration](configuration.md). |
| `native_instructions`, `set_native_instructions`, `readiness` | Native instruction/readiness dictionaries. |
| `models`, `integrations` | Provider-qualified catalog/observation dictionaries. |
| `validate_native_config(values)` | Normalized provider launch configuration dictionary. |
| `provider_versions`, `plan_provider_installation` | Version catalog / installation plan dictionaries; planning does not install. |
| `manage_profiles(tool, arguments, request_id=None)` | Structured administration result using the [management contract](profile-management-mcp.md). |

Except where a profile is positional, observation controls in this table are
keyword-only. `refresh=True` and `cached=True` conflict for budget inspection.
Unknown, stale, unavailable and unsupported allowance must not be treated as zero
or usable quota. Writes exposing `expected_revision` reject stale state using the operation's
revision domain (profile alias, environment, saved arguments, or instructions).
Use the matching observation's revision rather than interchanging revision strings.

## Errors, retries and lifecycle

| Public exception | Meaning / caller action |
| --- | --- |
| `Error` | Agent Manager failure, including identity mismatches, unsupported capabilities, busy guards, stale revisions and conflicting launch intent. Do not parse exception text as a stable error code. |
| `SelectionUnavailable` (`Error`) | No selection satisfies policy; `.code == 'selection_unavailable'`. Choose explicitly, change policy, or refresh evidence. |
| `LaunchError` (`Error`) | Launch failed, timed out, or has no acknowledged session yet. `.run_id` identifies retained intent. Inspect/reconcile it before attempting different work. |
| `RunNotFoundError` (`Error`) | No retained record exists for the requested run. |
| `ResourceError` (`Error`) | Structured administrative operation error with `code`, `message`, `uncertain`, `operation_id`, `recovery`, and `to_dict()`. |
| `ValueError`, `TypeError` | Invalid arguments; validation does not use one universal exception type. |
| Undercurrent exceptions | Runtime transport, observation and input failures propagate; use Undercurrent's structured error information. |

`asyncio.CancelledError` can propagate without stopping execution. Filesystem and
other underlying exceptions can also propagate; this is not an exhaustive exception
hierarchy. There is no SDK-wide implicit retry loop.

Supply a UUID `idempotency_key` and retain it **before** awaiting dispatch.
The same intent/key recovers accepted work without another process, even if a
launch-only workspace later disappears. Automatic retries retain the original
selection instead of reranking. Native argument/environment/configuration/context
changes conflict. Changing bridge path, RPC timeout or frame limits for the same
canonical Undercurrent home is a transport change and does not invalidate accepted
work. Changing runtime home does conflict. Accepted terminal executions are not
relaunched; use a new key for deliberately new work.

A timed-out call, cancelled wait or lost connection does not establish that work
stopped. Recover with the original key/reference or inspect the retained run.
Undercurrent `delivery_id` separately deduplicates terminal input; a launch key does
not deduplicate messages. Uncertain input errors must not be blindly retried with
new delivery IDs. Unknown observations retain administrative leases.
`execution_status` is read-only; reconciliation releases leases only after absence
or termination is established through the recorded runtime.

## Maintained example and package qualification

[The consumer example](../examples/sdk_consumer.py) supports inventory, automatic or
explicit launch, saved-session continuation, serialized-reference recovery, input,
and explicit stop. It uses existing profiles and a running Undercurrent daemon.

```sh
python examples/sdk_consumer.py inventory --provider codex --cwd /project
python examples/sdk_consumer.py launch --provider codex --cwd /project --reference execution.json
python examples/sdk_consumer.py recover --provider codex --reference execution.json --message 'Review the changes' --delivery-id review-1
python examples/sdk_consumer.py recover --provider codex --reference execution.json --stop
```

Pass `--profile main` to launch explicitly. Pass `continue --session REF` using a
reference printed by inventory; the saved workspace is used. Retain the printed
idempotency UUID for retries. Inventory uses cached-only budget reads; use `budget`
or `budgets(refresh=True)` to request fresh evidence.

The separate qualification command builds a clean package snapshot, installs
`[sdk]` in a new virtual environment, and runs an external consumer with fixture
Codex and an isolated real Undercurrent daemon:

```sh
python scripts/qualify_sdk.py --uc-bin /path/to/undercurrent/bin
```

For unpublished local dependencies:

```sh
python scripts/qualify_sdk.py --uc-bin /path/to/undercurrent/bin \
  --runtime-source ../../aixand-libraries/local-runtime \
  --undercurrent-source ../../undercurrent/python
```

It requires POSIX, Python 3.11+, venv/pip, and executable `uc-daemon`,
`uc-session-host`, `uc-python-bridge`. Missing prerequisites fail explicitly.
`--output NEW_DIRECTORY` retains artifacts at a chosen path; otherwise it prints
an automatically created temporary directory. Inspect `install.log`,
`qualification.log`, `daemon.log`, and `results.json`. The owned daemon is terminated
on success or failure; fixture executions are explicitly stopped during cleanup.
No real credentials, provider execution, model calls or allowance are used.

The check verifies packaged imports, dependency isolation, the maintained example's
inventory, ranking, spawning, state/watch, input/output/deduplication, detach,
reference recovery from another process, retry recovery, same-profile continuation,
and no duplicate executions. It does not qualify live provider behavior,
cross-profile continuation or other operating systems. Ordinary unittest discovery
does not invoke it and does not require Undercurrent binaries.

For opt-in checks against an existing signed-in profile, see
[live Codex qualification](live-sdk-qualification.md). It is separate from offline
qualification, can consume allowance, and documents the first live findings.

## Native maintenance worker API

`Manager.prepare_provider_operation(arguments)` validates and captures a native
provider operation without executing it. `Manager.perform_provider_operation(operation,
progress=callback)` executes the returned descriptor synchronously in the caller's
worker or terminal. `Client` exposes awaited equivalents. Both are public APIs
available from the root-exported `Manager` and `Client` classes.

Supported `kind` values are `install`, `update`, `repair`, `setup-command`,
`uninstall`, `disable`, `enable`, `profile-login` and `check-profile`.
Installation options are `method`, `versionTarget` and `reinstall`; terminal
registration uses `terminalPolicy`. Software removal/enablement requires the
observed `installationRevision` and explicit `confirmed` value. Profile operations
select `profile` and optionally guard its environment with `expectedRevision`.

Retain the prepared JSON descriptor before dispatch. It binds the provider,
manager registry, exact approved installation plan, or immutable environment and
profile identities plus environment revision. Executing a descriptor against
another registry or with a stale revision fails. Profile alias reuse does not
retarget captured native login. Capture does not install, authenticate or start a
runtime. Execution revalidates the captured intent and may invoke interactive
native software; provide an appropriate worker/terminal.

The progress callback receives Manager-owned progress dictionaries. Completion
returns the native operation result. A completed login command is distinct from
verified sign-in or model entitlement; inspect its `state` and `verification` and
current profile observations. Providers without a qualified status probe can
retain `unknown` with `native-command-completed` verification.

The caller owns operation IDs, durable dispatch/receipts, process supervision,
cancellation and recovery. These methods do not start a daemon or provide an
implicit retry loop. Losing a worker reply must not trigger blind replay of a
login or installation.
