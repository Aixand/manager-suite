# Python API map

Choose the public Python interface that owns the state you need to change or inspect.

## Choose an interface

| Need | Public import | Example |
| --- | --- | --- |
| Profile administration | `from agent_manager import Manager` | [Agent guide](../../agent-manager/docs/README.md) |
| Managed native execution | `from agent_manager import Client` | Async; requires Undercurrent SDK, binaries, and daemon |
| Retained programmatic chat | `from conversation_manager import ConversationClient` | [Conversation guide](../../conversation-manager/docs/README.md) |
| Prompt creation/rendering | `from prompt_manager import Manager` | [Render a prompt](../examples/render-prompt.md) |
| Skill library/project connection | `from skill_manager import Manager` | [Connect a skill](../examples/connect-skill.md) |
| MCP definition/resolution | `from mcp_manager import Manager` | [Resolve an MCP definition](../examples/resolve-mcp.md) |

`ConversationClient` and `conversation_manager.Client` name the same implementation. The different managers' `Manager` classes are independent APIs; alias imports when composing them in one application.

## Preserve the observed identity

Prompt and Skill package handles expose effective content and update status. Use their revision guards for edits and reconciliation. Skill project plans pin a preview and require explicit application.

MCP `add()` creates an absent definition. `configure(..., expected_revision=...)` changes it under a guard. `resolve()` computes selected configuration; health probing and live connection are separate operations.

Conversation operations return receipts and retained references. Inspect completion and recovery state. Disconnecting a frontend differs from stopping its worker. Agent execution handles retain `run_id` and Undercurrent session identity; closing a client is not process termination.

## Owning sources

- [Agent Manager exports](../../agent-manager/agent_manager/__init__.py).
- [Conversation Manager exports](../../conversation-manager/src/conversation_manager/__init__.py).
- [Prompt Manager exports](../../prompt-manager/src/prompt_manager/__init__.py).
- [Skill Manager exports](../../skill-manager/src/skill_manager/__init__.py).
- [MCP Manager facade](../../mcp-manager/src/mcp_manager/manager.py).

The examples target the current source checkout. Inspect these public contracts and installed versions before depending on a method found only in an old integration guide.
