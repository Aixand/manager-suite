"""CLI grammar; no lifecycle execution."""
import argparse
from ..._version import __version__
from ...errors import McpError


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise McpError("validation", message)


def parser():
    root = Parser(prog="mcp-manager", description="MCP definitions, project preparation and explicit runtime connectors")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--home", help="MCP store directory (no implicit migration)")
    commands = root.add_subparsers(dest="command", required=True)
    from .managed import install
    install(commands)
    for name in ("catalog", "list", "show", "add", "configure", "resolve", "prepare", "refresh", "status", "job", "jobs"):
        command = commands.add_parser(name)
        command.add_argument("--json", action="store_true", help="Emit a compact JSON result or structured error")
        if name in ("show", "add", "configure"):
            command.add_argument("name")
        if name in ("add", "configure"):
            command.add_argument("--definition", required=True, help="Inline JSON or @PATH")
        if name == "configure":
            command.add_argument("--expected-revision", required=True, help="Observed revision; none means create-only")
        if name == "resolve":
            command.add_argument("--servers", required=True, help="Ordered name array as JSON or @PATH")
            command.add_argument("--cwd", required=True)
            project = command.add_mutually_exclusive_group()
            project.add_argument("--project")
            project.add_argument("--no-project", action="store_true")
            command.add_argument("--strict", action="store_true")
            command.add_argument("--check", action="store_true")
        if name in ("prepare", "refresh"):
            command.add_argument("tools", nargs="+")
            command.add_argument("--project", required=True)
            command.add_argument("--key")
            command.add_argument("--background", action="store_true")
            command.add_argument("--settings", help="Preparation options as JSON or @PATH")
            command.add_argument("--resolve", action="store_true", default=None)
            command.add_argument("--install-model", action="store_true", default=None)
            command.add_argument("--model-dir")
            command.add_argument("--usage-config")
            command.add_argument("--resources", choices=("none", "yaml"))
        if name == "status":
            command.add_argument("--project", required=True)
            command.add_argument("--servers", help="Explicit name array, defaults to registered project presets")
            command.add_argument("--check", action="store_true", help="Read-only stdio freshness probes")
        if name == "job":
            command.add_argument("job_id")
        if name == "jobs":
            command.add_argument("--project")
    runtime = commands.add_parser("runtime")
    actions = runtime.add_subparsers(dest="runtime_action", required=True)
    for name in ("capabilities", "status", "reconnect", "operation"):
        command = actions.add_parser(name)
        command.add_argument("--json", action="store_true")
        if name == "operation":
            command.add_argument("operation_id")
            continue
        command.add_argument("--connector", required=True, help="Explicit MODULE:factory; factory(options) returns connector")
        command.add_argument("--target", required=True, help="Runtime target as JSON or @PATH")
        command.add_argument("--options", default="{}", help="Call-scoped factory options as JSON or @PATH")
        if name in ("status", "reconnect"):
            command.add_argument("--scope", required=True, choices=("server", "session", "process"))
            command.add_argument("--server-name")
        if name == "reconnect":
            command.add_argument("--operation-id", required=True)
            command.add_argument("--expected-selection-digest")
    return root

