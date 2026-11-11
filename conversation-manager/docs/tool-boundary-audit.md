# Exact tool-boundary delivery audit

Checked against installed Codex CLI **0.166.0** and OpenCode **2.2.5**.
This establishes the qualified adapter contract, not the absence of every possible
provider extension or hook.

Codex's locally generated `v2/TurnSteerParams.json` exposes
`clientUserMessageId`, `expectedTurnId`, `input`, and `threadId`. The active-turn
precondition prevents targeting a later turn; it does not name a tool call or
provide a tool-completion barrier. Our adapter invokes `turn/steer` with that
precondition. `item/completed` is an observed notification, not an acknowledgement
that the model is paused awaiting manager input.

Reproduce schema generation without credentials or inference:

```sh
codex --version
codex app-server generate-json-schema --out /tmp/codex-boundary-schema
```

An isolated OpenCode 2.2.5 server's `/openapi.json` exposes
`Session.Inbox.Delivery` as `steer | queue` and session inbox inspection/removal
routes. The only API path containing `pause` is an experimental scheduler pause
route, which is unrelated to holding an active turn at a tool boundary. The
adapter's native steering verifies an active session, submits with `resume=false`,
and removes stranded input if the native turn races completion. This does not
provide the required atomic tool-completion boundary either.

The OpenCode inspection used an isolated XDG configuration/database, a temporary
server credential, and only a schema read. No user service configuration was
changed and no model requests were made.

Therefore exact `after_tool` stays unsupported. Implementing it requires qualified
native semantics that capture the currently running tool set and guarantee input
admission after all of it finishes but before continuation. A timing-based test or
an asynchronous event handler cannot establish that guarantee. Any future hook
integration needs separate qualification, including parallel tools, tool failure,
turn completion, disconnect, cancellation and unknown outcomes.


## Observed tool completion

The current product uses **At next tool completion** (`next_tool_completion`),
which deliberately does not require the stricter guarantee audited above.
Normalized tool events update a captured set of in-flight tool IDs. When that set
finishes, the worker immediately uses the adapter's ordinary same-turn steering
path with an exact turn precondition. If the turn has already ended, it retains
the text rather than starting another turn. Codex emits tool start/result events;
OpenCode emits stateful tool updates, including terminal failures. The normalizer
handles both. This mode is available where steering is supported and a tool is
currently observed running. Its offline tests do not claim new live provider
qualification or paused native continuation.
