# Read turns, events and transcripts

Track the exact turn returned by a send receipt. Acceptance and observed completion are different outcomes.

## Wait for the requested turn

This excerpt assumes an attached `conversation`. A production frontend also
handles pending requests and displays errors; bound every wait.

```python
import asyncio

sent = await conversation.send('Explain this project', operation_id='send-1')
if sent['status'] not in ('accepted', 'completed'):
    raise RuntimeError(sent)
turn_id = sent['result']['turn_id']
async with asyncio.timeout(120):
    async for event in conversation.events():
        if event['kind'] == 'turn.completed' and event['turn_id'] == turn_id:
            break
result = await conversation.turn_result(turn_id)
```

Inspect `result['status']`: terminal outcomes include completed, cancelled and
failed. A `turn.completed` event marks terminal observation; it is not by itself
a success assertion. A timeout only ends the frontend wait, so inspect retained
state before cancelling or retrying. The event iterator can end when closed;
missing canonical output remains unresolved.

## Persist the correct cursor

| Read | Cursor and meaning |
| --- | --- |
| `conversation.events(after_sequence=...)` | Event sequence; defaults to replay from zero |
| `connection.event_page(..., after_sequence=...)` | Event page cursor, optional wait and closed indicator |
| `conversation.history(cursor=..., limit=...)` | Separate history cursor, entries and `has_more` |
| `conversation.turn_result(turn_id)` | Canonical artifact for one observed terminal turn |
| `conversation.transcript()` | Provider-backed display baseline where implemented |

Order direct and queued input by observed event sequence. Do not interchange
history and event cursors. Retained history is manager-observed evidence, not an
assertion that every native message is in the manager journal.

## Rebuild the Codex display

`transcript()` reads Codex's saved thread without sending or resuming work. It
returns provider/source metadata, normalized items, native identifiers and a
`complete` flag. Other adapters currently return `None`. Known native turns retain
manager IDs so tool and supervision anchors survive attachment.

The in-memory view merges newer messages by item ID. During streaming native
reads coalesce to at most once per two seconds; turn transitions reconcile
immediately. Honor `reconcile_pending` and `retry_after_ms` after activity stops.
An unavailable read retains cached and live items with `complete: false`; retries
back off to thirty seconds. Display partial/unavailable state. This read does not
rewrite the delivery journal or create a second durable transcript.

## Inspect provider background work

Where advertised, use `background_activity()`, `background_agent(id)`, and
`background_process(id)` to read verified conversation-owned work. Explicit
`stop_background_process(id, operation_id=...)` is a separate mutation. A
foreground terminal result does not imply all provider background work stopped.

[Turn artifacts](../src/conversation_manager/turn_output.py),
[transcript view](../src/conversation_manager/transcript.py),
[background inspection](../src/conversation_manager/background.py), and
[transcript tests](../tests/test_transcript.py) define these views.
