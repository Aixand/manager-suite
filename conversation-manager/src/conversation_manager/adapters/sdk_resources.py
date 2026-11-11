"""Validated SDK configuration; credentials remain transient worker inputs."""

import json
import os
import re
from pathlib import Path
import shutil

from ..models import ConversationError, digest
from ..skill_delivery import verify_export


def validate(settings):
    if set(settings) - {
        "cwd",
        "model",
        "mode",
        "instructions",
        "skills",
        "mcp",
        "access",
    }:
        raise ConversationError("unsupported", "Unknown SDK session settings")
    if settings.get("mode", "default") not in ("default", "plan"):
        raise ConversationError(
            "unsupported", "SDK conversation mode must be default or plan"
        )
    if settings.get("model") is not None and (
        not isinstance(settings["model"], str) or not settings["model"].strip()
    ):
        raise ConversationError("validation", "SDK model must be a nonempty string")
    instructions = settings.get("instructions")
    if instructions and (
        not isinstance(instructions, dict)
        or not isinstance(instructions.get("text"), str)
    ):
        raise ConversationError("validation", "SDK instructions require captured text")
    for skill in settings.get("skills", []):
        verify_export(skill)
    access = settings.get("access") or {}
    if access and (
        not isinstance(access, dict)
        or set(access) != {"permission_mode"}
        or access["permission_mode"] not in ("ask", "deny")
    ):
        raise ConversationError(
            "unsupported",
            "SDK access accepts permission_mode ask or deny; no Codex sandbox equivalence is claimed",
        )
    mcp = settings.get("mcp") or {}
    seen = set()
    for row in mcp.get("servers", []):
        name = row.get("server_name", row.get("name"))
        if not isinstance(name, str) or not name or name in seen:
            raise ConversationError("validation", "SDK MCP servers require unique names")
        seen.add(name)
        if row.get("transport", row.get("type", "stdio")) != "stdio":
            raise ConversationError(
                "unsupported",
                "Only captured stdio MCP servers are qualified for SDK delivery",
            )
        if (
            not isinstance(row.get("command"), str)
            or not row["command"]
            or not isinstance(row.get("args", []), list)
        ):
            raise ConversationError(
                "validation", "SDK MCP server requires command and argument list"
            )
        if any(
            not isinstance(arg, str) or "\0" in arg
            for arg in [row["command"], *row.get("args", [])]
        ):
            raise ConversationError("validation", "Invalid SDK MCP arguments")
        if row.get("cwd") not in (None, settings.get("cwd")):
            raise ConversationError(
                "unsupported",
                "SDK MCP working directory must match the captured workspace",
            )
        if row.get("env") not in (None, {}, []):
            raise ConversationError(
                "validation",
                "MCP credential values must use transient environment bindings",
            )
        bindings = row.get("env_vars", [])
        if not isinstance(bindings, list) or any(
            not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
            for key in bindings
        ):
            raise ConversationError(
                "validation", "MCP environment bindings must be variable names"
            )


def mcp_servers(settings, env):
    result = {}
    for row in (settings.get("mcp") or {}).get("servers", []):
        name = row.get("server_name", row.get("name"))
        if any(key not in env for key in row.get("env_vars", [])):
            raise ConversationError(
                "authentication",
                "Supply all captured MCP environment bindings when starting or restoring the SDK worker",
            )
        variables = {key: env[key] for key in row.get("env_vars", []) if key in env}
        result[name] = {
            "command": row["command"],
            "args": row.get("args", []),
            "env": variables,
        }
    return result


def claude_plugin(settings, directory):
    """Copy and verify packages instead of enabling ambient skill discovery."""
    skills = settings.get("skills", [])
    if not skills:
        return [], []
    root = Path(directory) / "managed-plugin"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = root / ".claude-plugin"
    metadata.mkdir(mode=0o700, exist_ok=True)
    (metadata / "plugin.json").write_text(
        json.dumps({"name": "manager-resources", "version": "1.0.0"})
    )
    for row in skills:
        target = root / "skills" / row["name"]
        if target.exists():
            verify_export({**row, "export": str(target)})
        else:
            shutil.copytree(row["export"], target)
            verify_export({**row, "export": str(target)})
    return [{"type": "local", "path": str(root)}], [
        "manager-resources:" + row["name"] for row in skills
    ]


def session_directory(store, prepared, identity):
    root = (
        store.home
        / "sdk-sessions"
        / digest({"adapter": prepared.adapter_id, "identity": identity})[7:]
    )
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root
