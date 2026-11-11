"""Retained Claude transcript validation and guarded native resume arguments."""
from pathlib import Path
import uuid

from agent_manager.errors import Error


def transcript(home, native_id):
    try:
        identifier = str(uuid.UUID(native_id))
    except (ValueError, TypeError, AttributeError):
        raise Error("Claude continuation requires an exact native session UUID") from None
    if native_id != identifier: raise Error("Claude continuation requires an exact native session UUID")
    root = Path(home).resolve() / "projects"
    if root.is_symlink(): raise Error("The original Claude projects directory must not be a symlink")
    paths = [path.resolve() for path in root.glob("*/" + identifier + ".jsonl")
             if path.is_file() and path.resolve().is_relative_to(root)]
    if len(paths) != 1:
        raise Error("The original Claude transcript is unavailable or ambiguous; select it in the native session picker")
    return paths[0]


VALUE_OPTIONS = {"--model", "--fallback-model", "--permission-mode", "--permission-prompt-tool",
        "--permission-prompts", "--setting-sources", "--settings", "--mcp-config",
        "--system-prompt", "--append-system-prompt", "--system-prompt-file", "--append-system-prompt-file",
        "--system-prompt-snapshot", "--input-format", "--output-format", "--json-schema",
        "--debug-file", "--agent", "--agents", "--effort", "--name", "-n", "--max-turns", "--max-budget-usd"}


def validate_arguments(arguments, native_id, *, resume_target=None):
    """A provider preference must not turn a retained resume into a fork."""
    incompatible = {"--fork-session", "--continue", "-c", "--session-id", "--from-pr",
        "--cloud", "--teleport", "--bg", "--background", "--desktop", "--worktree", "-w",
        "--resume-session-at", "--resume-drops-turn", "--rewind-files", "--no-session-persistence", "--"}
    # Skip literal scalar values, including prompt text that resembles a flag.
    values = VALUE_OPTIONS
    arguments = list(arguments)
    index, resumed, resume_option = 1, False, None
    while index < len(arguments):
        key, equals, inline = arguments[index].partition("=")
        if key.startswith("-") and not key.startswith("--") and len(key) > 2:
            raise Error("Claude continuation requires separate short options; combined options can change the selected session")
        if key in incompatible:
            raise Error("Retained Claude continuation cannot use " + key + "; remove it from the provider launch arguments")
        if key in ("--resume", "-r"):
            value = inline if equals else arguments[index + 1] if index + 1 < len(arguments) else None
            if resumed or value != (resume_target or native_id):
                raise Error("Claude continuation must resume the exact original native session once")
            resumed = True
            resume_option = (index, bool(equals))
            index += 1 if equals else 2
        else:
            index += 2 if key in values and not equals else 1
    if not resumed:
        raise Error("Claude continuation must resume the exact original native session once")

    return resume_option


def discover(home, *, known=()):
    """Read only native envelope metadata; never retain message bodies."""
    import json
    root = Path(home).resolve() / 'projects'
    if root.is_symlink(): return []
    result = []
    for path in root.glob('*/*.jsonl'):
        try:
            identifier = str(uuid.UUID(path.stem))
            if identifier != path.stem or identifier in known: continue
            if transcript(home, identifier) != path.resolve(): continue
            cwd = None
            with path.open(encoding='utf-8') as stream:
                for _ in range(32):
                    line = stream.readline(1048577)
                    if not line or len(line) > 1048576: break
                    envelope = json.loads(line)
                    if isinstance(envelope, dict) and isinstance(envelope.get('cwd'), str):
                        cwd = Path(envelope['cwd'])
                        break
            if cwd is not None and cwd.is_absolute():
                result.append(dict(native_id=identifier, cwd=str(cwd.resolve()), updated=path.stat().st_mtime, rollout_path=str(path.resolve())))
        except (OSError, ValueError, Error, RuntimeError): continue
    return result
