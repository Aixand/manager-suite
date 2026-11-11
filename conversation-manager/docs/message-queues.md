# Message queues

Queues belong to Conversation Manager's retained worker and SQLite store, not a
browser tab. They survive frontend disconnection. Stopping/replacing a worker
never automatically replays its retained queue on another native session.

`await conversation.queue_state()` returns `items`, `paused`, `reason` and
`modes`. Session state includes the same queue snapshot. Each mode reports
`supported` and, where unsupported, a reason. Older running workers do not have
this API; create a new conversation on an updated worker to enable it.

```python
state = await conversation.state()
queued = await conversation.enqueue(
    'Also check the tests', mode='after_turn',
    expected_turn_id=state['active_turn']['turn_id'], operation_id='queue-1',
)
# Inspect queued['status']; a successful enqueue is not a successful model turn.
await conversation.edit_queued('queue-1', 'Check the integration tests', operation_id='edit-1')
await conversation.remove_queued('queue-1', operation_id='remove-1')
await conversation.pause_queue(operation_id='pause-1')
await conversation.resume_queue(operation_id='resume-1')
```

`after_turn` accepts plain text against an exact currently active turn. It sends
one FIFO message as a new turn after a confirmed successful completion, then
waits for that turn before sending the next message. A stale target or a turn
already stopping is rejected. Slash-command execution and settings are separate
operations, not queued prompt interpretations.

Cancellation requests, failed/uncertain outcomes, and approval/question callbacks
pause the queue. Answering a question does not implicitly resume it. Resolve
pending requests and explicitly resume. Resuming while idle starts the next
queued message; resuming while a turn is active waits for its successful end.
Never resume after an unconfirmed provider outcome without inspecting it first.

A delivery gets its own deterministic operation ID, separate from enqueue. The
worker persists dispatch intent before calling the provider. It retains the send
receipt and turn ID on the queue item. Failed/uncertain sends pause subsequent
work. A `dispatching` record left by interrupted dispatch is not replayed, and
resume is rejected until its outcome is inspected. Removing/editing is allowed
only before dispatch. Successful handoff is `delivered`, not model completion;
use `turn_result(item['turn_id'])` for the latter. Normal send receipts preserve
their own lifecycle independently of the enqueue receipt.

`queue.updated` records changes in the event stream/history; its payload contains
item identities/status and pause reason. Use `queue_state` for current text and
full receipts. `message.input` with `delivery='after_turn'` records the delivered
user input under its new turn. Frontends should order turns by observed event
sequence, including both directly sent and queued messages.

Limits: 100 unresolved items, 32,000 characters per message and 128,000 total
unresolved text characters. The state snapshot retains the last 20 removed or
delivered items; input history and operation receipts remain separate.

## At next tool completion

Use `mode='next_tool_completion'` with the exact active turn ID. This mode is
advertised when the adapter supports steering and is available only while tools
are observed running. Enqueue captures their IDs in `captured_tools`; completion
notifications remove IDs from `remaining_tools`. Parallel calls in the captured
set must all finish, including failed calls. Tools started later do not delay that
message. The queue snapshot exposes `running_tools` for frontend availability.

When the captured set finishes, the worker steers the message into the original
turn. This is **observed-completion timing**: the agent may already have continued
before the manager receives the event. It does not pause or interrupt the turn,
claim a native execution barrier, or start a new turn. Messages in each delivery
mode preserve FIFO order; tool guidance does not wait behind after-turn messages.

If the original turn ends or is cancelled first, the queued text remains retained
and the queue pauses. It is never silently converted into an after-turn send.
Resume rejects guidance targeting an ended turn; remove the retained item and
explicitly send the text elsewhere if that is desired. Approvals/questions and
unknown delivery still use the existing explicit pause/recovery rules.

Guidance delivery has its own deterministic `queue-steer:` operation and exact
turn precondition. Its `message.input` event has `delivery='steer'`,
`delivery_mode='next_tool_completion'` and the queue ID, under the original turn.
A confirmed steering receipt proves handoff, not model consumption. In particular,
completion of the original turn does **not** prove an uncertain steering input
was handed off. Inspection/reconciliation validates the steering intent separately.

The `after_tool` name is unsupported because it implies
an exact native barrier. Use `next_tool_completion` for the practical behavior.
See [the boundary audit](tool-boundary-audit.md) for the distinction.

## Inspect and reconcile

`await conversation.inspect_queued(id)` and
`await client.inspect_queued(reference, id)` read retained evidence even when the
worker is stopped. `client.recorded_queue(reference)` provides the last persisted
queue without connecting. These reads do not establish worker availability.

The inspection returns the stored item, a matching receipt where available, a
canonical terminal result where observed, an `outcome`, and `can_reconcile`.
`accepted` means a new-turn send was handed off; `handed_off` means steering was handed off. Neither proves model consumption. `not_dispatched` means an item was
queued/removed before delivery; `unconfirmed` means delivery is not established;
`intent_conflict` means the retained operation is for another intent. Terminal
outcomes remain `completed`, `cancelled`, or `failed`.

If `can_reconcile` is true, call
`conversation.reconcile_queued(id, operation_id=...)` on the original available
worker. It validates the exact immutable session/content intent and receipt,
updates the queue, restores missing input history if needed, and leaves remaining
messages paused. It never sends another prompt. Missing or conflicting evidence
cannot be reconciled. Inspection is not authorization for automatic replay.

Queue items include a `revision` starting at 1. When capabilities advertise
`queue_revision: 1`, callers can pass `expected_revision` to `edit_queued` or
`remove_queued`; the worker rejects stale edits atomically before changing the
queue. The argument is optional for existing clients. Do not assume older
retained workers enforce revisions without checking their capabilities.
