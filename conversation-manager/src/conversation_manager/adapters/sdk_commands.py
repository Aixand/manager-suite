"""Command mappings qualified for the pinned SDKs, with explicit provenance."""

import re

from ..models import ConversationError

INSPECTIONS = {
    "context": "Inspect SDK conversation context",
    "usage": "Inspect observed session token usage and API cost",
    "mcp": "Inspect captured session MCP servers",
    "skills": "Inspect captured session skill packages",
}
SUITE_ALIASES = {
    "help",
    "new",
    "clear",
    "resume",
    "agents",
    "subagents",
    "copy",
    "mention",
    "quit",
    "exit",
    "raw",
    "warnings",
    "export",
    "diff",
    "pwd",
    "cwd",
}
NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,255}")
PROMPT_COMMANDS = {
    "code-review",
    "verify",
    "debug",
    "simplify",
    "security-review",
    "recap",
    "init",
}


def catalog(protocol, settings, discovered=(), terminal=()):
    claude = protocol == "claude-agent-sdk"
    captured = {
        "manager-resources:" + row["name"] for row in settings.get("skills", [])
    }
    rows = {}
    for entry in discovered:
        entry = {"name": entry} if isinstance(entry, str) else entry
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not NAME.fullmatch(entry["name"])
            or entry["name"].startswith("__")
        ):
            continue
        name = entry["name"]
        row = {
            "name": name,
            "description": str(entry.get("description") or name),
            "inputHint": entry.get("argumentHint"),
            "execution": "unsupported",
            "requiresIdle": True,
            "source": "provider",
            "reason": "This SDK command has no qualified retained-session mapping.",
        }
        if name in terminal:
            row["reason"] = "This provider command requires an interactive terminal."
        elif name in SUITE_ALIASES:
            row["reason"] = (
                "The application owns this conversation lifecycle or interface action."
            )
        elif claude and name in {"compact", *PROMPT_COMMANDS}:
            row.update(execution="native", reason=None, acceptsArguments=True)
        elif claude and name in captured:
            row.update(
                execution="native",
                reason=None,
                source="captured-skill",
                acceptsArguments=True,
            )
        rows[name] = row
    for name, description in INSPECTIONS.items():
        rows[name] = {
            "name": name,
            "description": description,
            "execution": "native",
            "requiresIdle": False,
            "source": "sdk-inspection",
        }
    rows["permissions"] = {
        "name": "permissions",
        "description": "Choose the SDK tool approval policy",
        "execution": "setting",
        "configId": "permission_mode",
        "requiresIdle": True,
        "source": "manager-policy",
    }
    if protocol == "copilot-sdk":
        rows["model"] = dict(name="model", description="Change the confirmed Copilot model", execution="setting",
                             configId="model", requiresIdle=True, source="sdk-control")
        return {"state": "ready", "commands": list(rows.values())}
    if claude:
        rows["plan"] = {
            "name": "plan",
            "description": "Switch the acknowledged SDK planning mode",
            "execution": "setting",
            "configId": "mode",
            "requiresIdle": True,
            "source": "sdk-control",
        }
        rows["model"] = {
            "name": "model",
            "description": "Change the acknowledged SDK model",
            "execution": "setting",
            "configId": "model",
            "requiresIdle": True,
            "source": "sdk-control",
        }
    else:
        rows["model"] = {
            "name": "model",
            "description": "Choose the session model",
            "execution": "unsupported",
            "source": "sdk-control",
            "reason": "Select the Antigravity model when creating a new session.",
        }
        rows["compact"] = {
            "name": "compact",
            "description": "Compact conversation context",
            "execution": "unsupported",
            "source": "provider",
            "reason": "The pinned Antigravity SDK has no qualified manual-compaction API.",
        }
        for skill in settings.get("skills", []):
            rows[skill["name"]] = {
                "name": skill["name"],
                "description": "Captured skill " + skill["name"],
                "execution": "unsupported",
                "source": "captured-skill",
                "reason": "This SDK loads the captured skill for agent use; direct slash invocation is not qualified.",
            }
    return {"state": "ready", "commands": list(rows.values())}


def validate(snapshot, name, arguments):
    if not isinstance(arguments, str) or len(arguments) > 24000 or "\0" in arguments:
        raise ConversationError("validation", "Command arguments must be bounded UTF-8 text")
    row = next((row for row in snapshot["commands"] if row["name"] == name), None)
    if row is None:
        raise ConversationError(
            "unsupported", "Choose a command advertised by this SDK session"
        )
    if row["execution"] != "native":
        raise ConversationError(
            "unsupported", row.get("reason") or "Use the advertised session setting"
        )
    if name in INSPECTIONS and arguments:
        raise ConversationError(
            "validation", "This inspection command does not accept arguments"
        )
    return row


def skills(settings):
    return {
        "source": "captured-resources",
        "skills": [
            {key: row[key] for key in ("name", "digest", "export")}
            for row in settings.get("skills", [])
        ],
    }


def permission_option(settings):
    return {
        "id": "permission_mode",
        "category": "permissions",
        "name": "Tool approvals",
        "type": "select",
        "currentValue": (settings.get("access") or {}).get("permission_mode", "ask"),
        "options": [
            {"value": "ask", "name": "Ask for approval"},
            {"value": "deny", "name": "Deny approval prompts"},
        ],
    }
