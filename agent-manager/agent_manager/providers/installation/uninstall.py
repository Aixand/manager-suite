"""Explicit ownership-checked provider CLI removal."""

import builtins
import json
import sys

from agent_manager.errors import Error
from agent_manager.interfaces.cli import tui
from agent_manager.manager import Manager
from agent_manager.providers.installation import engine as install
from agent_manager.providers.installation import software


def parser(parser_type):
    cli = parser_type(
        prog="agent uninstall",
        description="Uninstall a provider CLI without deleting its profiles, authentication or native homes.",
        epilog="""Ownership-verified native, npm and Homebrew installations can be removed.
Bundled and unverifiable installations are preserved with manual removal guidance.

Examples:
  agent uninstall codex
  agent uninstall claude --dry-run
  agent uninstall opencode --yes""",
    )
    cli.add_argument("target", nargs="?", metavar="PROVIDER")
    cli.add_argument("--yes", "-y", action="store_true", help="Uninstall without prompting")
    cli.add_argument("--dry-run", action="store_true", help="Inspect the removal plan without changing files")
    cli.add_argument("--json", action="store_true", help="Print the plan or result as JSON")
    return cli


def _public(manager, plan):
    return dict(
        provider=manager.provider,
        path=plan["path"],
        version=plan["version"],
        method=plan["method"],
        automatic=plan["automatic"],
        reason=plan["reason"],
        targets=plan["targets"],
        command=plan.get('command', []),
        commandTargets=plan["commandTargets"],
        manualCommands=plan["manualCommands"],
        preservesProfiles=True,
        preservesAuthentication=True,
        preservesNativeHomes=True,
    )


def _display(manager, plan, *, json_output=False):
    public = _public(manager, plan)
    if json_output:
        print(json.dumps(public, indent=2))
        return
    print(f"Provider CLI uninstall plan for {manager.provider}")
    print("  executable: " + (plan["path"] or "not installed"))
    if plan["version"]:
        print("  version: " + plan["version"])
    print("  action: " + ("uninstall" if plan["automatic"] else "preserve"))
    for target in [*plan["targets"], *plan["commandTargets"]]:
        print("  remove: " + target)
    if plan.get('command'):
        import shlex
        print('  command: ' + shlex.join(plan['command']))
    if plan["reason"]:
        print("  reason: " + plan["reason"])
    print("  preserve: profiles, authentication, settings and native homes")
    for command in plan["manualCommands"] if not plan["automatic"] else ():
        print("  manual: " + command)


def _choose(config, home, picker):
    rows = []
    for provider in install.catalog():
        manager = Manager(provider, config=config, root=home)
        plan = software.plan(manager)
        if plan["path"] is not None:
            rows.append((manager, plan))
    if not rows:
        raise Error("No supported provider CLI is installed")
    disabled = {index for index, (_, plan) in enumerate(rows) if not plan["automatic"]}
    selected = picker(
        "Uninstall an agent CLI",
        ["Provider", "Version", "Executable", "Status"],
        [
            (
                manager.provider,
                plan["version"] or "Unknown",
                plan["path"],
                "Ready" if plan["automatic"] else plan["reason"],
            )
            for manager, plan in rows
        ],
        "Only ownership-verified installations can be selected.",
        disabled=disabled,
        initial=next((index for index in range(len(rows)) if index not in disabled), 0),
    )
    return None if selected is None else rows[selected]


def command(args, *, config=None, home=None, picker=None, input_fn=None):
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if args.target is None:
        if args.yes or args.dry_run or args.json or not interactive:
            raise Error("Specify a provider, for example: agent uninstall codex")
        selected = _choose(config, home, picker or tui.pick)
        if selected is None:
            return 0
        manager, plan = selected
    else:
        manager = Manager(args.target, config=config, root=home)
        plan = software.plan(manager)
    if args.dry_run:
        _display(manager, plan, json_output=args.json)
        return 0
    if not args.json:
        _display(manager, plan)
    if plan["path"] is None:
        raise Error("No provider CLI is installed for " + manager.provider)
    if not plan["automatic"]:
        raise Error(plan["reason"] or "This provider CLI requires manual removal")
    if not args.yes:
        if not interactive:
            raise Error("Uninstall requires an interactive terminal or --yes")
        phrase = "UNINSTALL " + manager.provider.upper() + " CLI"
        if (input_fn or builtins.input)(
            f"Type {phrase} to remove only the listed provider CLI installation files: "
        ).strip() != phrase:
            print("Uninstall cancelled.")
            return 0
    result = software.perform(
        manager,
        "uninstall",
        revision=plan["revision"],
        confirmed=True,
    )
    result = dict(provider=manager.provider, **result)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Uninstalled {manager.provider} provider CLI.")
        print("Preserved profiles, authentication, settings and native homes.")
    return 0
