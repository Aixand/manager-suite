# Codex server isolation

Separate servers remain the default. Applications may opt into sharing while
retaining one Conversation Manager worker and one native connection per chat.

```python
chat = await client.start_conversation(
    agent=selected_agent,
    cwd=workspace,
    settings={"access": {"sandbox": "read-only", "approvalPolicy": "on-request"}},
    codex_server_mode="shared",  # "separate" is the default
    operation_id="stable-startup-id",
)
```

The lower-level `launch_codex` API accepts `server_mode="shared"`. The mode is
part of durable launch intent; retrying an operation with another mode is a
conflict. Restore retains the original mode and native thread identity.

## Ownership and isolation

`SharedCodexRuntime` acquires a server from a private local pool. Pool identity
includes the Conversation Manager storage scope, exact Agent Manager native
environment descriptor/revision, and resolved working directory. Aliases are
excluded because they are display metadata. Different environments, revisions,
workspaces, or stores do not share. Agent Manager revalidates the environment
before reuse; the pool owner holds the environment lease until shutdown.

The independent owner launches Codex on a private local WebSocket and uses
`NativeGateway` to provide each conversation with its own upstream connection.
Conversation workers retain their queues, operation receipts, settings, and
approval ownership. Closing a worker closes its connection, not the shared
server. RPC identifiers and approval responses stay on the receiving connection.

After all clients disconnect for 60 seconds, the owner checks gateway activity
under the admission lock. It stops only when no turns, pending requests, queues,
background commands, goals, logins, remote control, or uncertain activity block
shutdown. An unavailable activity inspection prevents shutdown. This does not
automatically suspend idle conversation workers: attached chats still retain
their connection and therefore keep the pool alive.

The pool uses PID plus process-start identity and a fresh epoch for each server.
Concurrent launch requests serialize; an unconfirmed live launch is not replaced.
A confirmed dead owner can be replaced for a new startup. Failed in-flight turns
are not automatically replayed. In Suite, reconnecting a conversation after
shared transport loss stops only that disconnected worker and invokes the
existing exact-thread restore path. Recovery does not claim an uncertain action
failed or repeat it.

Shared mode is currently disabled on Windows pending native listener and
lifecycle qualification. Separate mode remains available there. The benchmark
and process-memory sampler are Linux-specific; macOS has not been benchmarked.

## Suite setting

Settings → Agent behavior → Codex process isolation offers separate/shared mode.
The policy is stored in the orchestration execution database, with revision
checks and browser broadcasts. It is resolved when a new execution starts, so
direct chats, delegated tasks, and workflow conversations using the same
execution service inherit it. Explicit `codex_server_mode` overrides the default.
Existing executions and operation retries retain their original selection.

## Five-conversation benchmark

Measured on Linux on 2026-11-13, three runs per mode, alternating order. Each
conversation used `gpt-6.1-sol`, read-only access, the same disposable workspace,
and one tiny reply without tool calls. All 30 replies matched their requested
text. The totals include the five conversation workers and their descendants;
shared totals also include the owner, gateway, process guard, and native server.
The test driver and existing user processes are excluded. Existing user workloads
continued during measurement; the machine was not otherwise isolated.

| Stage | Separate servers, mean PSS | Shared server, mean PSS | Reduction |
|---|---:|---:|---:|
| Idle before work | 545.2 MiB | 277.9 MiB | 49.0% |
| Sampled active peak | 601.1 MiB | 299.2 MiB | 50.2% |
| Idle after work | 609.8 MiB | 294.7 MiB | 51.7% |

PSS apportions shared physical pages rather than counting them in full for each
process. Active peaks were sampled approximately every 250 ms and may miss short
spikes. The active peak above is the mean of each trial's sampled peak. Startup
was sequential; inference was concurrent. Reply timing is affected by remote
inference and is not evidence of a general speed advantage.

A separate single-conversation baseline (one run per mode) measured 123.7 MiB
versus 145.5 MiB at sampled active peak: shared mode used 17.6% more RAM. The
extra owner/gateway overhead pays off as more compatible conversations share it;
sharing is not a memory optimization for every chat count.

These figures do not establish savings for long chats, tool-heavy workloads,
different models, other operating systems, or different numbers of compatible
conversations. MCP/tool processes and loaded conversation state still consume
memory. Use the percentage only with the five-conversation lightweight-test
qualification, not as a guaranteed reduction.

Raw results: [codex-sharing-benchmark.json](codex-sharing-benchmark.json).

Reproduce using an authenticated Agent Manager environment:

```sh
python conversation-manager/scripts/benchmark_codex_sharing.py \
  --count 5 --repetitions 3 --output /tmp/codex-sharing-results.json
```

The benchmark makes small inference requests and stops only its own test
processes. `--idle-only` avoids inference. The saved native test threads remain
in the selected environment's history; no existing user threads are removed.

## Validation

The focused checks cover five concurrent workers sharing one native server,
per-thread outputs and mixed policies, approval ownership, stopping one worker,
workspace separation, abrupt server death and exact-thread restore, and stable
launch retries. Existing native/gateway and Suite chat-route regression tests
also pass. The Suite browser test covers saving, reload persistence, broadcast
updates, and stale-revision failures. Web and server source type checks pass.
The broader server type check that includes e2e tests still reports unrelated
`supertest/types` and auth-test cast errors.

A real-server check verified different MCP tools under the same server name,
different native sandbox policies, and independent reasoning-setting mutations:

```sh
python conversation-manager/scripts/verify_codex_sharing.py
```

The live idle-owner check also confirmed automatic shutdown after the final
worker disconnected and the 60-second inactivity period elapsed. These live
checks were run on Linux; Windows shared mode remains disabled and macOS
qualification remains outstanding.
