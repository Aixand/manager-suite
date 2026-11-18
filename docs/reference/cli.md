# CLI reference

Find each manager command and the parser that owns its installed options.

## Command map

| Command | Start here | Current parser |
| --- | --- | --- |
| `agent` | `agent --help`, `agent codex --help` | [Agent CLI](../../agent-manager/agent_manager/interfaces/cli/parser.py) |
| `conversation-manager` | `conversation-manager --help` | [Conversation CLI](../../conversation-manager/src/conversation_manager/cli.py) |
| `mcp-manager` | `mcp-manager --help` | [MCP CLI](../../mcp-manager/src/mcp_manager/interfaces/cli/application.py) |
| `prompt-manager` | `prompt-manager --help` | [Prompt CLI](../../prompt-manager/src/prompt_manager/interfaces/cli/dispatch.py) |
| `skill-manager` | `skill-manager --help` | [Skill CLI](../../skill-manager/src/skill_manager/cli.py) |

Use the installed command's help for exact options. `manager-suite` is an aggregate product ID, not a general-purpose manager command.

## Agent Manager

Provider-scoped commands include `onboarding`, `profile`, `environment`, `login`, `doctor`, `run`, `start`, `sessions`, `continue`, `budget`, and `models`. Software installation/update commands have their own forms. Saved session selection and execution profiles are different inputs.

## Conversation Manager

Use `demo --fixture --json` for the offline lifecycle demonstration. Frontend commands include `agents`, `conversation start`, `conversation list`, and `conversation stop-startup`. Live startup needs an observed agent reference and authenticated provider environment.

## Resource managers

Prompt, Skill, and MCP managers expose distinct `managed` operations. Do not assume one manager's flags or returned schemas apply to another. Supply `--home` before the command to select a dedicated store.

Skill project commands include `import`, `connect`, `connections`, `update-connections`, `disconnect`, and `apply-project-plan`. Prompt commands include `render`, `validate`, `release`, `diff`, and `store`. MCP commands separate definition, preparation, source/install, and runtime connector operations.

## Retired entry points

Server Manager is replaced by Conversation Manager in current builds. Agent Manager no longer exposes the old delegated prompt/skill/MCP resource workflow. The resource-sources package is also retired. Follow the owning manager's current parser instead of archived command examples.
