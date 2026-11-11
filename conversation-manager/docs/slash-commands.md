# Slash-command discovery SDK

`await conversation.list_slash_commands(available_only=False)` returns a JSON
dictionary with schema `conversation-manager/slash-commands-1`, backend ID,
catalog state, source, scope, completeness, and a `commands` list.

Each entry contains:

| Field | Meaning |
| --- | --- |
| `name`, `slash` | Bare invocation name and display form, for example `review` and `/review`. Nested names retain their separators. |
| `argument_options` | Native advertised value/name choices for argument completion; empty when not applicable. |
| `description`, `argument_hint` | Description and provider/adapter argument hint, or null when no hint is known. |
| `execution` | `native`, `setting`, or `unsupported`. |
| `available` | Catalog is ready and the adapter provides a command or settings mapping. This is support metadata, not a guarantee that a busy session can execute immediately. |
| `executable` | Can be invoked as a native command through `conversation.command`. This distinguishes native actions from setting mappings; common setting aliases can also be submitted through `command`. |
| `setting_id` | Configuration identifier for `set_config` when execution is `setting`. |
| `requires_idle` | Whether the mapping requires an idle conversation, or null when unknown. |
| `terminal_required`, `reason` | Known terminal requirement and reason an entry is unavailable. |
| `source` | Adapter/native provenance. |

`available_only=True` filters out unsupported entries while preserving settings
controls. To show only command invocations, filter for `executable`. Settings
values come from `conversation.configuration()`, not from inferred argument text.
Use `conversation.state()` when deciding whether an idle-only action can run.

```python
catalog = await conversation.list_slash_commands(available_only=True)
invocations = [row for row in catalog['commands'] if row['executable']]
```

The corresponding lower-level method is
`await connection.list_slash_commands(session_ref, available_only=False)`.
`commands()` retains the original adapter catalog shape. The CLI offers
`conversation slash-commands --connection @connection.json --conversation
@conversation.json [--available-only] --json`.

## Provider scope

Codex reports its reviewed CLI reference inventory, then qualifies native and
settings mappings against connected session metadata. Its provenance identifies
the reference date; this is not a claim of live enumeration of every plugin,
skill, custom command, or terminal action.

OpenCode V2 initializes native agents for the conversation's exact working
directory, then reads `GET /api/command`. Agent initialization registers built-in
callbacks; querying the command endpoint first can incorrectly return an empty
catalog. The returned list combines live callbacks with qualified native session
API mappings. On version `2.2.5`, advertised `/review` and `/init` callbacks retain
streaming, requests, cancellation and observed parent-session completion. Other
callbacks remain unavailable until their lifecycle is qualified.

Both providers expose common setting aliases for model, reasoning/effort,
permissions, mode/plan and speed/fast. Their `argument_options` come from the live
configuration catalog. Native model/service-tier values remain provider-specific.
`command('reasoning', 'high', ...)` and `set_config('effort', 'high', ...)` use the
same setting implementation and durable mutation rules. No-argument ordinary
setting commands return their option; `/plan` and `/fast` select plan/advertised
fast mode, while `status` inspects and `off` resets to the advertised default.

OpenCode maps compaction, rename, session diff, status, configuration inspection,
usage and MCP status to its native APIs. Codex's diff command reads native
file-change items. Settings and command argument discovery never execute these
actions or perform model inference. Catalog scope remains incomplete: this is
not the full TUI or configured prompt-template inventory. A missing native
endpoint reports unsupported discovery; malformed replies and transport failures
remain errors.

```python
receipt = await conversation.command('review', 'HEAD', operation_id='review-1')
# Inspect receipt status before using its turn ID.
turn_id = receipt['result']['turn_id']
async for event in conversation.events():
    if event['kind'] == 'turn.completed' and event['turn_id'] == turn_id:
        break
result = await conversation.turn_result(turn_id)
```

Callback acknowledgement is not completion: only an observed native idle outcome
finishes the turn. OpenCode does not return a stable input ID for this route, so
the manager retains its local turn ID and never invents a native input identity.
Same-ID retries inspect the retained receipt without dispatching again. Lost
acknowledgements, post-handoff read failures, and missing completion stay uncertain.
Uncertain commands block further sends on that session until explicit runtime
recovery. Callbacks that do not produce foreground parent-session completion time
out as uncertain after 120 seconds; acknowledgement alone is never success.

Neither provider currently claims a complete inventory (`complete=False`). The
SDK does not read or expand command templates, run shell blocks, invoke commands,
or send model requests during discovery. Refresh the list explicitly after
changing models, settings, or provider configuration; no command-list subscription
is provided yet.

Native endpoint reference: [OpenCode V2 command API](https://dev.opencode.ai/v2/docs/api).
