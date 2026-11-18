# Change session settings

Build controls from the connected session configuration. Values vary by provider and model; do not hard-code a universal permissions or speed catalog.


Both initial providers share `configuration()`, `set_config()`, command discovery,
argument choices, native command execution and durable operation receipts. Common
controls are model, reasoning effort (`effort`), service tier (`serviceTier`),
conversation mode (`mode`) and permissions. OpenCode also exposes its native
`variant` selector. Values remain provider/model-specific and are never invented.

```python
options = await conversation.configuration()
commands = await conversation.list_slash_commands(available_only=True)
await conversation.command('reasoning', 'high', operation_id='reasoning-1')
await conversation.command('plan', 'on', operation_id='planning-1')
# Select actual advertised permission values for the connected provider.
await conversation.set_config('permissions', selected_permission, operation_id='permissions-1')
```

`/model`, `/reasoning`, `/effort`, `/permissions`, `/mode`, `/plan`, `/speed` and
`/fast` use the same setting-command path. With no argument, ordinary setting
commands return their choices; `/plan` selects planning mode and `/fast` selects
an advertised fast/priority tier. `off` selects default mode/tier and `status`
returns the current option without changing it. Unsupported speed/effort choices
are rejected before mutation. Changing reasoning preserves an advertised speed
combination and vice versa. Model changes clear incompatible variants rather than
applying an old model's options to a different model.

Settings and setting commands require an idle session. The worker excludes new
sends/other settings mutations during an update, checks native readback, and
pauses pending queue delivery if the update is unconfirmed. Retrying the same
operation inspects its original receipt instead of applying the setting again.
Normal settings changes and direct inspection commands do not invoke a model.
Review, initialization and compaction may invoke the model and retain turn
completion/cancellation semantics. Provider-native and terminal-only commands
outside these chat mappings still report explicit support information; this is
not an emulation of every TUI action or a promise that every model has a fast tier.


## Verify a change

Inspect the receipt, refresh `configuration()`, and show the confirmed current
value. If readback is unconfirmed, keep queue delivery paused and inspect the
original operation; retrying with a new ID can apply another mutation.

[Native controls](../src/conversation_manager/native_controls.py),
[OpenCode controls](../src/conversation_manager/opencode_controls.py), and
[control tests](../tests/test_native_controls.py) own these mappings.
Use [slash discovery](slash-commands.md) for command execution metadata.
