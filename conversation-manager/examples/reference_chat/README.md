# Reference chat frontend

A maintained browser example of Conversation Manager's public Python SDK. It
includes messaging, streamed results, approval/question responses, cancellation,
history, detach/reconnect, advertised settings/slash choices, queues and recovery.
The Python HTTP bridge uses the SDK; the browser does not call provider APIs.

Run from the Conversation Manager checkout on Python 3.11+ and a supported local
POSIX system. Install the SDK and platform sibling from source if needed:

```sh
python -m venv .venv
.venv/bin/python -m pip install -e ../../aixand-libraries/filesystem -e ../../aixand-libraries/local-runtime -e .
.venv/bin/python examples/reference_chat/app.py --port 8847
```

Open http://127.0.0.1:8847. Select **Offline SDK demo**; it needs no provider
credentials or inference. Try approval/question buttons, `hold` and cancellation,
`queue-demo` and follow-up messages, `/compact`, settings, and disconnect/reload.
The fixture echoes input; it is not a Codex or OpenCode model simulation.

## Live agents and network access

Install Agent Manager and native chat dependencies, then opt in to discovery:

```sh
.venv/bin/python -m pip install -e ../agent-manager -e '.[agents,native-codex]'
.venv/bin/python examples/reference_chat/app.py --port 8847 --live
```

Live selection discovers configured Codex/OpenCode environments through Agent
Manager. Supply an absolute working directory and optionally a model. Live
messages use the selected account's quota. Discovery is not a credential or
entitlement check. OpenCode requires a configured environment and qualified
installed version. Agent config/state roots may be supplied through
`--agent-config` and `--agent-root`.

For network access, explicitly bind and allow the address used in the browser:

```sh
.venv/bin/python examples/reference_chat/app.py --host 0.0.0.0 \
  --allowed-host YOUR_IP --port 8847 --live
```

Host and Origin checks restrict requests to configured addresses. This is a
single-user development example without login, TLS, attachments or terminals.
Use it on a trusted machine/network; it is not an internet-facing deployment.

## Retained state and recovery

The default state is `examples/reference_chat/.state` (ignored by Git). Override
with `--state /absolute/path` to keep state outside the checkout or reuse a
previous reference app's recordings. It stores UI metadata and SDK journals.
Do not run two web bridges against the same UI state at once.

Browser detach, refresh, and **web bridge shutdown retain workers**. Restart with
the same state to attach recorded conversations. **Stop backend** explicitly
terminates the runtime and all chats sharing it; it does not delete history.
`--stop-workers-on-exit` is an opt-in cleanup flag for isolated tests: it only
cleans up startups requested by that bridge run, not every startup in its state.

Use **Inspector → Recovery** or **Inspect delivery** on a queued item. Read-only
inspection works after the worker stops and distinguishes a recorded successful
startup from present reachability. A matching confirmed delivery can be
reconciled on the original available worker without resending; the remaining
queue stays paused. Missing, conflicting or uncertain evidence offers no resend.
A stopped worker's retained queue is not automatically moved to another session.

Settings pickers use only advertised values. Type `/model `, `/permissions `,
`/reasoning ` or `/fast ` where supported. Filter by typing; Arrow Down enters
choices, Enter selects, Escape closes. Changing models refreshes dependent
choices. Settings are unavailable during active turns.

After-turn queues are worker-owned and persist through frontend disconnection.
Approvals/questions, cancellation, and failed/unknown outcomes pause delivery
until explicit resume. **At next tool completion** captures currently running tools and steers into the
same turn once all are observed finished. It becomes selectable while tools run.
The agent may already have continued. If the turn ends first, guidance remains
queued and is not sent as a new turn. For an offline check, send **Tool queue demo**,
select that delivery mode and queue guidance; a later tool continues while the
captured pair finishes. Guidance appears inside the original turn and survives
reload. See [the boundary audit](../../docs/tool-boundary-audit.md) for the
difference from an exact native barrier.
Older retained workers need replacement through an explicit new startup before
new worker features become available; refreshing a browser does not update them.

## Reproducible browser verification

With the SDK installed as above:

```sh
.venv/bin/python -m pip install -r examples/reference_chat/tests/requirements.txt
.venv/bin/python -m playwright install chromium --only-shell
.venv/bin/python examples/reference_chat/tests/run_browser_checks.py
.venv/bin/python -m unittest discover -s examples/reference_chat/tests -p 'test_*.py'
```

The runner starts its own loopback server on an allocated port, uses temporary
state with offline fixtures, runs chat/settings/after-turn queue/tool-completion guidance/recovery browser flows, and
stops only its own workers. It does not use live agents or the running user app.
Reports, screenshots and server logs go to `.artifacts` (ignored by Git).
`PLAYWRIGHT_BROWSERS_PATH` may point at an existing matching browser installation.

Individual scripts accept `REFERENCE_CHAT_URL` and `REFERENCE_CHAT_ARTIFACTS`.
Those scripts create demo conversations in the supplied running app; the isolated
runner is the default for repeatable checks. SDK tests, including interrupted
queue dispatch/reconciliation, run with `python -m unittest discover -s tests`.
