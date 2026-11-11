"""Normalize explicit project preparation options."""
from pathlib import Path
from ..errors import McpError


def options(value=None):
    if value is not None and not isinstance(value, dict):
        raise McpError("validation", "MCP preparation options must be an object")
    value = dict(value or {})
    if set(value) - {"resolve", "model_dir", "usage_config", "resources", "install_model"}:
        raise McpError("validation", "Unknown MCP preparation option")
    for key in ("resolve", "install_model"):
        if key in value and not isinstance(value[key], bool):
            raise McpError("validation", f"{key} must be a boolean")
    for key in ("model_dir", "usage_config"):
        if key in value:
            if not isinstance(value[key], str) or not value[key]:
                raise McpError("validation", f"{key} must be a path")
            value[key] = str(Path(value[key]).expanduser().resolve())
    if value.get("resources", "none") not in ("none", "yaml"):
        raise McpError("validation", "Sonar resources must be none or yaml")
    return value

