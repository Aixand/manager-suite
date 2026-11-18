# Answer approvals and questions

Render the pending request offered by the current conversation. Never infer a native approval value from a display label.

## Inspect and answer

Read `await conversation.pending_requests()` after `request.pending` or attachment.
Each request records its request ID, session/turn identities, transport epoch,
kind, choices and optional response schema. Present choices or schema-required
inputs to the user. Keep an operation ID for the response.

For an offered choice-based permission request with no response schema, use this
async excerpt after the user selects an actual `optionId`:

```python
pending = await conversation.pending_requests()
request = pending[0]
selected_option_id = request['choices'][0]['optionId']
receipt = await conversation.respond(
    request['request_id'],
    {'outcome': 'selected', 'option_id': selected_option_id},
    operation_id='answer-1',
)
```

The first option is an example selection, not an approval policy. The alternative
choice-based cancellation response is `{'outcome': 'cancelled'}`. Requests with a
response schema, including native questions, instead require
`{'native_result': ...}` matching that schema. Preserve provider-native decision
and answer structures; the wrapper does not translate arbitrary prose into them.

## Observe delivery and resume deliberately

A request is durably reserved before dispatch so concurrent answers cannot both
win. Stale, resolved, reserved or previous-epoch requests conflict. SDK callbacks
can return an accepted response receipt while delivery is still pending; inspect
the original receipt and request state rather than claiming completion.
`Conversation.respond` verifies the request still belongs to this conversation.

An approval/question pauses the queue. Answering does not resume it. After the
request resolves and the outcome is known, call `resume_queue(operation_id=...)`
explicitly if delivery should continue. Do not convert an uncertain response into
a new approval request or silently approve during reconnect.

The [offline controls example](examples/offline-controls.md) demonstrates a
fixture denial and confirms the terminal turn. The [reference chat](../examples/reference_chat/README.md)
shows user-facing controls without requiring a provider account.

[Request validation and reservation](../src/conversation_manager/core.py),
[native request mapping](../src/conversation_manager/native_requests.py), and
[SDK callback ownership](../src/conversation_manager/adapters/sdk_session.py)
own these contracts. [Queue approval tests](../tests/test_message_queue.py)
verify explicit pause and resume behavior.
