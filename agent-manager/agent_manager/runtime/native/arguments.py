"""Native subcommand and explicit session identity parsing."""
import uuid
from pathlib import Path
from agent_manager.errors import Error

def command(args):
    """Locate the native subcommand after supported global CLI options."""
    values = {
        "-c",
        "--config",
        "--enable",
        "--disable",
        "-p",
        "--profile",
        "-C",
        "--cd",
        "-m",
        "--model",
        "-s",
        "--sandbox",
        "-a",
        "--ask-for-approval",
        "--add-dir",
        "-i",
        "--image",
        "--local-provider",
        "--remote",
        "--remote-auth-token-env",
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return list(args[index + 1 :])
        if arg in values:
            index += 2
        elif arg.startswith("-"):
            index += 1
        else:
            return list(args[index:])
    return []


def resume_id(args):
    values = command(args)
    if values[:1] == ['resume']:
        try: return str(uuid.UUID(values[1]))
        except (IndexError, ValueError, AttributeError): pass
    return None


def working_directory(args, cwd):
    cwd = Path(cwd).resolve()
    for index, arg in enumerate(args):
        if arg in ("--worktree", "--remote") or arg.startswith("--remote="):
            raise Error(
                "Cross-environment continuation requires a local conversation with a fixed working directory"
            )
        if arg in ("-C", "--cd"):
            if index + 1 >= len(args):
                raise Error("Missing Codex working directory")
            cwd = (cwd / Path(args[index + 1]).expanduser()).resolve()
        elif arg.startswith("--cd="):
            cwd = (cwd / Path(arg.split("=", 1)[1]).expanduser()).resolve()
        elif arg.startswith("-C") and len(arg) > 2:
            cwd = (cwd / Path(arg[2:]).expanduser()).resolve()
    return cwd
