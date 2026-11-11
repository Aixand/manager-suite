"""Codex CLI command inventory, reviewed against the official CLI guide.

Reference: https://learn.chatgpt.com/docs/developer-commands (2026-10-15).
Descriptions are application wording. A terminal command is not an RPC mapping.
"""

COMMANDS = {
    "permissions": "Choose session permissions",
    "ide": "Attach editor context",
    "keymap": "Configure terminal keyboard bindings",
    "vim": "Configure terminal Vim editing",
    "setup-default-sandbox": "Configure the Windows sandbox",
    "agents": "Browse conversations",
    "subagents": "Browse child conversations",
    "apps": "Browse connected applications",
    "plugins": "Manage provider plugins",
    "hooks": "Manage lifecycle hooks",
    "clear": "Start a fresh conversation",
    "rename": "Rename this conversation",
    "archive": "Archive the provider session",
    "delete": "Delete the provider session",
    "compact": "Compact conversation context",
    "copy": "Copy the latest assistant response",
    "diff": "Inspect project changes",
    "exit": "Leave this conversation",
    "experimental": "Configure provider experiments",
    "approve": "Retry an auto-review denial",
    "memories": "Configure provider memory",
    "skills": "Browse and invoke native skills",
    "import": "Import another agent setup",
    "feedback": "Send provider diagnostics",
    "init": "Create project agent instructions",
    "logout": "Sign out of the provider",
    "mcp": "Inspect session MCP servers",
    "mention": "Attach a project file",
    "model": "Choose the conversation model",
    "fast": "Choose the conversation speed",
    "plan": "Switch planning mode",
    "goal": "Inspect or change the conversation goal",
    "ps": "Inspect provider background terminals",
    "stop": "Stop provider background terminals",
    "fork": "Branch this conversation",
    "app": "Continue in the desktop application",
    "side": "Open a separate follow-up conversation",
    "btw": "Open a separate follow-up conversation",
    "raw": "Use plain transcript output",
    "resume": "Open a saved conversation",
    "new": "Start a conversation in this project",
    "quit": "Leave this conversation",
    "review": "Review project changes",
    "status": "Inspect conversation settings",
    "usage": "Inspect provider rate limits",
    "debug-config": "Inspect effective session settings",
    "statusline": "Configure the terminal footer",
    "title": "Configure the terminal title",
    "theme": "Configure the terminal syntax theme",
    "pets": "Configure the terminal pet",
    "pet": "Configure the terminal pet",
    "tui": "Configure the terminal display",
    "export": "Export the saved conversation",
    "recap": "Summarize without compacting",
    "cd": "Change the working directory",
    "pwd": "Show the working directory",
    "cwd": "Show the working directory",
    "worktree": "Manage provider Git worktrees",
    "daemon": "Manage the provider background server",
    "warnings": "Inspect conversation warnings",
}

TURN_COMMANDS = frozenset(("compact", "review", "init", "recap"))
DIRECT_COMMANDS = frozenset(
    (
        "rename",
        "mcp",
        "usage",
        "ps",
        "stop",
        "goal",
        "apps",
        "skills",
        "status",
        "debug-config",
        "diff",
    )
)


def inventory():
    return [
        dict(
            name=name,
            description=description,
            execution="native"
            if name in TURN_COMMANDS | DIRECT_COMMANDS
            else "unsupported",
            requiresIdle=name in TURN_COMMANDS,
            **(
                {}
                if name in TURN_COMMANDS | DIRECT_COMMANDS
                else {
                    "reason": "This command requires the Codex terminal interface.",
                    "terminal": True,
                }
            ),
        )
        for name, description in COMMANDS.items()
    ]
