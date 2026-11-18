# Own and stop worker processes

Choose standalone or Undercurrent ownership for new workers. Detaching a frontend does not terminate a retained worker.


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


## Verify ownership

Inspect the saved runtime reference before cleanup. A termination receipt is not
proof of exit; inspect again until the owner reports a terminal lifecycle.
See [runtime selection](../src/conversation_manager/runtime.py),
[Undercurrent owner](../src/conversation_manager/undercurrent_owner.py), and
[owner tests](../tests/test_undercurrent_owner.py).
