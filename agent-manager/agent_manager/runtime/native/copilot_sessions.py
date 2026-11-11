"""Exact retained Copilot resumes; source history always stays canonical."""
from pathlib import Path
import uuid

from agent_manager.errors import Error


def event_log(home, native_id):
    try:
        identifier = str(uuid.UUID(native_id))
    except (ValueError, TypeError, AttributeError):
        raise Error("Copilot continuation requires an exact native session UUID") from None
    if native_id != identifier:
        raise Error("Copilot continuation requires an exact native session UUID")
    directory = Path(home).resolve() / "session-state" / identifier
    paths = (directory / "events.jsonl", directory / "workspace.yaml")
    try:
        available = all(path.resolve() == path and path.is_file() for path in paths)
    except (OSError, RuntimeError):
        available = False
    if not available:
        raise Error("The original Copilot session history or workspace is unavailable; select it in the native session picker")
    return paths[0]


VALUE_OPTIONS = {"--model", "--reasoning-effort", "--context", "--auto-tier", "--agent", "--name", "-n",
        "--prompt", "-p", "--interactive", "-i", "--log-dir", "--log-level", "--stream",
        "--output-format", "--extension-sdk-path", "--add-dir", "--attachment", "--disable-mcp-server",
        "--enable-mcp-server", "--add-github-mcp-toolset", "--add-github-mcp-tool", "--plugin-dir",
        "--additional-mcp-config", "--mcp-github-auth", "--max-autopilot-continues", "--mode",
        "--host-compute-id", "--dynamic-retrieval", "--max-ai-credits", "--usage-output-file"}


def validate_arguments(arguments, native_id):
    """Do not let provider preferences replace the saved session or home."""
    incompatible = {"--continue", "--session-id", "--connect", "--fork", "--fork-session",
        "--worktree", "-w", "-C", "--config-dir", "--auth-token-env", "--acp", "--stdio",
        "--headless", "--host-environment", "--host-ahp", "--port", "--"}
    values = VALUE_OPTIONS
    optional_values = {"--share", "--sync-session", "--bash-env", "--mouse"}
    multiple_values = {"--allow-tool", "--deny-tool", "--available-tools", "--excluded-tools",
        "--secret-env-vars", "--allow-url", "--deny-url"}
    arguments = list(arguments)
    index, resumed = 1, False
    while index < len(arguments):
        key, equals, inline = arguments[index].partition("=")
        if key.startswith("-") and not key.startswith("--") and len(key) > 2:
            raise Error("Copilot continuation requires separate short options; combined options can change the selected session")
        if key in incompatible:
            raise Error("Retained Copilot continuation cannot use " + key + "; remove it from the provider launch arguments")
        if key in ("--resume", "-r"):
            value = inline if equals else arguments[index + 1] if index + 1 < len(arguments) else None
            if resumed or value != native_id:
                raise Error("Copilot continuation must resume the exact original native session once")
            resumed = True
            index += 1 if equals else 2
        elif key in values and not equals:
            index += 2
        elif key in optional_values | multiple_values and not equals:
            index += 1
            while index < len(arguments) and not arguments[index].startswith("-"):
                index += 1
                if key in optional_values:
                    break
        elif not key.startswith("-"):
            raise Error("Retained Copilot continuation cannot invoke a subcommand or supply positional overrides")
        else:
            index += 1
    if not resumed:
        raise Error("Copilot continuation must resume the exact original native session once")


def discover(home, *, known=()):
    """Read workspace identity and directory scalars, without opening event bodies."""
    import json
    root = Path(home).resolve() / 'session-state'
    if root.is_symlink(): return []
    result = []
    for directory in root.iterdir() if root.is_dir() else ():
        try:
            identifier = str(uuid.UUID(directory.name))
            if identifier != directory.name or identifier in known: continue
            events = event_log(home, identifier)
            workspace = directory / 'workspace.yaml'
            if workspace.stat().st_size > 65536: continue
            fields = {}
            for line in workspace.read_text(encoding='utf-8').splitlines():
                key, colon, value = line.partition(':')
                if key not in ('id', 'cwd') or not colon: continue
                value = value.strip()
                if value.startswith('"'): value = json.loads(value)
                elif value.startswith("'") and value.endswith("'"): value = value[1:-1].replace("''", "'")
                fields[key] = value
            cwd = Path(fields['cwd'])
            if fields.get('id', identifier) != identifier or not cwd.is_absolute(): continue
            result.append(dict(native_id=identifier, cwd=str(cwd.resolve()), updated=events.stat().st_mtime))
        except (OSError, ValueError, KeyError, Error, RuntimeError): continue
    return result
