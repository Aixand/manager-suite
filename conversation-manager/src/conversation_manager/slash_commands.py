"""Frontend command metadata; discovery never grants execution support."""

from copy import deepcopy


def catalog(value, *, backend_id, available_only=False):
    state = value.get("state", "unsupported")
    result = []
    for row in value.get("commands", []):
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        name = row["name"].lstrip("/")
        if not name:
            continue
        execution = row.get("execution", "unsupported")
        if execution not in ("native", "setting", "unsupported"):
            execution = "unsupported"
        available = state == "ready" and execution in ("native", "setting")
        if available_only and not available:
            continue
        result.append(
            dict(
                name=name,
                slash="/" + name,
                description=row.get("description") or name,
                argument_hint=row.get("inputHint"),
                argument_options=deepcopy(row.get("options", [])),
                execution=execution,
                available=available,
                executable=available and execution == "native",
                setting_id=row.get("configId") if execution == "setting" else None,
                requires_idle=row.get("requiresIdle"),
                terminal_required=not available and bool(row.get("terminal")),
                source=row.get("source", value.get("source")),
                reason=None
                if available
                else row.get("reason") or "No supported execution mapping is available",
            )
        )
    return dict(
        schema="conversation-manager/slash-commands-1",
        backend_id=backend_id,
        state=state,
        source=value.get("source"),
        scope=value.get("scope", "adapter-catalog"),
        complete=value.get("complete", False),
        commands=result,
    )


SETTING_COMMANDS = {
    "model": "model",
    "reasoning": "effort",
    "effort": "effort",
    "permissions": "permissions",
    "mode": "mode",
    "plan": "mode",
    "fast": "serviceTier",
    "speed": "serviceTier",
    "variant": "variant",
}


def setting_selection(name, arguments, configuration):
    """Resolve a setting command using actual native choices; never invent tiers."""
    from .models import ConversationError

    key = SETTING_COMMANDS[name]
    option = next((row for row in configuration["options"] if row["id"] == key), None)
    if option is None:
        raise ConversationError(
            "unsupported", "The selected model does not advertise this setting"
        )
    value = arguments.strip()
    if value == "status" or not value and name not in ("plan", "fast"):
        return key, None, option
    if name == "plan":
        value = {"": "plan", "on": "plan", "off": "default"}.get(value, value)
    if name == "fast":
        if value in ("", "on"):
            value = next(
                (
                    row["value"]
                    for row in option["options"]
                    if row["value"] in ("fast", "priority")
                ),
                None,
            )
            if value is None:
                raise ConversationError(
                    "unsupported",
                    "The selected model does not advertise a fast service tier",
                )
        elif value == "off":
            value = "default"
    return key, value, option
