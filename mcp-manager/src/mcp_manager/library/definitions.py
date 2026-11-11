"""Definition validation and revision fingerprints."""
import hashlib
import json
import os
from pathlib import Path
import re

from ..errors import McpError
from ..storage.files import name

AUTO = object()
PREFIX = "agent_manager_"
PRESETS = {}
PROJECT_KINDS = ("sonar", "lore")

def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def preparation_digest(value):
    """Only execution/index inputs invalidate a prepared generation."""
    return digest({key: item for key, item in value.items()
                   if key not in {"startup_timeout_sec", "tool_timeout_sec", "cwd"}})


def names(values):
    if not isinstance(values, (list, tuple)) or len(values) > 64:
        raise McpError("validation", "MCP names must be an array of at most 64 names")
    if any(not isinstance(value, str) for value in values):
        raise McpError("validation", "MCP names must be strings")
    return list(dict.fromkeys(name(value) for value in values))


def definition(identifier, value):
    name(identifier)
    if not isinstance(value, dict) or set(value) - {"kind", "command", "args", "env_vars", "cwd", "startup_timeout_sec", "tool_timeout_sec", "installation"}:
        raise McpError("validation", "Invalid MCP definition fields")
    row = {"kind": "stdio", "args": [], "env_vars": [], "startup_timeout_sec": 60,
           "tool_timeout_sec": 60, **value}
    if row["kind"] not in ("stdio", "sonar", "lore") or (row["kind"] != "stdio" and row["kind"] != identifier):
        raise McpError("validation", "Built-in MCP presets must use their own names")
    if not isinstance(row.get("command"), str) or not row["command"] or "\0" in row["command"]:
        raise McpError("validation", "MCP command must be a nonempty executable name or path")
    if not isinstance(row["args"], list) or len(row["args"]) > 128 or any(not isinstance(arg, str) or "\0" in arg for arg in row["args"]):
        raise McpError("validation", "MCP arguments must be an array of strings")
    if not isinstance(row["env_vars"], list) or any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) for key in row["env_vars"]):
        raise McpError("validation", "MCP environment references must be variable names")
    if "cwd" in row:
        if not isinstance(row["cwd"], str) or not row["cwd"]:
            raise McpError("validation", "MCP working directory must be a path")
        row["cwd"] = str(Path(row["cwd"]).expanduser().resolve())
    for key in ("startup_timeout_sec", "tool_timeout_sec"):
        if isinstance(row[key], bool) or not isinstance(row[key], (int, float)) or not 0 < row[key] <= 3600:
            raise McpError("validation", f"MCP {key} must be between 0 and 3600 seconds")
    if "installation" in row:
        from ..installations.recipes import validate
        validate(row["installation"])
    if os.sep in row["command"] or (os.altsep and os.altsep in row["command"]):
        # Interpreter symlinks select their virtual environment. Dereferencing
        # one changes the executable's import path and can break captured MCPs.
        row["command"] = str(Path(row["command"]).expanduser().absolute())
    return row
