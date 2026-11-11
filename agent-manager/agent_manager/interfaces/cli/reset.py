"""Global destructive reset command with explicit data, home and CLI boundaries."""

import builtins
import json
import sys

from agent_manager.errors import Error
from agent_manager.interfaces.cli import tui
from agent_manager.storage import reset


def parser(parser_type):
    cli = parser_type(
        prog="agent reset",
        description="Delete Agent Manager data, registered native homes and optionally provider CLIs.",
        epilog="""Without a provider, choose all-provider data reset or full factory reset.
Provider configuration, default native homes and CLI removal have separate boundaries.
Only ownership-verified native CLI installations can be removed automatically.
Close all provider sessions first.

Examples:
  agent reset --factory --dry-run
  agent reset --all-providers
  agent reset codex
  agent reset codex --yes --reset-config
  agent reset --factory --yes
  agent reset codex --yes --uninstall-clis
  agent reset --factory --yes --include-default-homes --uninstall-clis""",
    )
    cli.add_argument(
        "provider",
        nargs="?",
        metavar="PROVIDER",
        help="Reset only this provider; omit to choose a global reset scope",
    )
    scopes = cli.add_mutually_exclusive_group()
    scopes.add_argument(
        "--all-providers",
        action="store_true",
        help="Reset every provider while preserving shared Agent Manager configuration",
    )
    scopes.add_argument(
        "--factory",
        action="store_true",
        help="Delete all Agent Manager configuration, provider data and internal state",
    )
    cli.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Confirm the reset without prompting; default homes remain protected",
    )
    cli.add_argument(
        "--include-default-homes",
        action="store_true",
        help="With --yes, also delete native default homes such as ~/.codex",
    )
    cli.add_argument(
        "--reset-config",
        action="store_true",
        help="For one provider, restore its Agent Manager configuration defaults",
    )
    cli.add_argument(
        "--uninstall-clis",
        action="store_true",
        help="With --yes, also uninstall ownership-verified provider CLIs",
    )
    cli.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the deletion plan without changing files",
    )
    cli.add_argument(
        "--json", action="store_true", help="Print the plan or result as JSON"
    )
    return cli


def _cli_plans(plan):
    from agent_manager.manager import Manager
    from agent_manager.providers.config import load_registry
    from agent_manager.providers.installation import software

    registry = load_registry(plan.config, allow_missing=True)
    providers = (plan.provider,) if plan.provider else tuple(sorted(registry.definitions))
    result = []
    for provider in providers:
        manager = Manager(
            provider,
            config=plan.config,
            root=plan.root,
            _allow_missing_config=True,
        )
        candidate = software.plan(manager)
        if candidate["path"] is not None:
            result.append((manager, candidate))
    return result


def _public_cli_plans(cli_plans):
    return [
        dict(
            provider=manager.provider,
            path=candidate["path"],
            version=candidate["version"],
            method=candidate["method"],
            automatic=candidate["automatic"],
            reason=candidate["reason"],
            targets=candidate["targets"],
            commandTargets=candidate["commandTargets"],
            manualCommands=candidate["manualCommands"],
        )
        for manager, candidate in cli_plans
    ]


def _display(plan, cli_plans, *, json_output=False):
    if json_output:
        print(
            json.dumps(
                {**plan.public(), "providerClis": _public_cli_plans(cli_plans)},
                indent=2,
            )
        )
        return
    label = (
        " for " + plan.provider
        if plan.scope == "provider"
        else " for all providers"
        if plan.scope == "providers"
        else " (factory reset)"
    )
    print("Agent Manager reset plan" + label)
    print("Manager data:")
    for path in plan.manager_paths:
        print("  delete " + str(path))
    ordinary = [home for home in plan.homes if not home.default]
    defaults = [home for home in plan.homes if home.default]
    print("Registered non-default native homes:")
    for home in ordinary:
        print("  delete " + str(home.path))
    if not ordinary:
        print("  none")
    print("Protected native default homes (separate confirmation):")
    for home in defaults:
        print("  " + str(home.path))
    if not defaults:
        print("  none")
    configuration = plan.configuration_reset
    if plan.scope == "provider":
        print("Provider Agent Manager configuration:")
        if not configuration.available:
            print("  already using bundled defaults")
        elif configuration.requested:
            for key in configuration.preference_keys:
                print("  restore preference table " + key + " to bundled defaults")
            for path in configuration.manifest_paths:
                print("  delete local provider definition " + str(path))
        else:
            print("  preserve current provider overrides")
    elif plan.scope == "providers":
        print("Shared Agent Manager configuration: preserve")
    else:
        print("Shared Agent Manager configuration: delete (factory defaults on next use)")
    print("Installed provider CLIs (separate confirmation):")
    for manager, candidate in cli_plans:
        action = "can uninstall" if candidate["automatic"] else "keep"
        details = candidate["reason"] or candidate["method"] or "unknown method"
        print(
            f"  {action} {manager.provider}: {candidate['path']}"
            + (f" ({candidate['version']})" if candidate["version"] else "")
            + f" — {details}"
        )
        for target in [*candidate["targets"], *candidate["commandTargets"]]:
            print("    remove " + target)
    if not cli_plans:
        print("  none")


def _scope(args, *, interactive, picker):
    if args.provider is not None:
        if args.all_providers or args.factory:
            raise Error("Choose a provider reset or a global reset scope, not both")
        return "provider"
    if args.reset_config:
        raise Error("--reset-config requires a specific provider")
    if args.all_providers:
        return "providers"
    if args.factory:
        return "factory"
    if args.yes or not interactive:
        raise Error("Choose --all-providers or --factory when no provider is supplied")
    selected = picker(
        "Choose reset scope",
        ["Scope", "Deletes", "Preserves"],
        [
            (
                "All providers",
                "Profiles, managed homes and provider state",
                "Manager configuration and shared services",
            ),
            (
                "Factory reset",
                "All Manager configuration, provider data and state",
                "Separately protected native defaults and CLIs",
            ),
        ],
        "Review the exact filesystem plan before confirmation.",
    )
    return None if selected is None else ("providers" if selected == 0 else "factory")


def command(args, *, config=None, home=None, input_fn=None, picker=None):
    if args.dry_run and args.yes:
        raise Error("--dry-run cannot be combined with --yes")
    if args.dry_run and args.include_default_homes:
        raise Error("--dry-run cannot be combined with --include-default-homes")
    if args.dry_run and args.uninstall_clis:
        raise Error("--dry-run cannot be combined with --uninstall-clis")
    if args.uninstall_clis and not args.yes:
        raise Error("--uninstall-clis requires --yes; interactive reset asks separately")
    if args.reset_config and not (args.yes or args.dry_run):
        raise Error("--reset-config requires --yes; interactive provider reset asks separately")
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    scope = _scope(args, interactive=interactive, picker=picker or tui.pick)
    if scope is None:
        print("Reset cancelled.")
        return 0
    read = input_fn or builtins.input
    plan = reset.create_plan(
        provider=args.provider,
        config=config,
        root=home,
        scope=scope,
        reset_provider_config=args.reset_config,
    )
    if (
        scope == "provider"
        and plan.configuration_reset.available
        and not args.yes
        and not args.dry_run
    ):
        phrase = "RESET " + plan.provider.upper() + " CONFIG"
        answer = read(
            f"Restore {plan.provider} Agent Manager configuration to bundled defaults? "
            f"Type {phrase}, or press Enter to preserve it: "
        )
        if answer.strip() == phrase:
            plan = reset.create_plan(
                provider=plan.provider,
                config=config,
                root=home,
                scope=scope,
                reset_provider_config=True,
            )
    cli_plans = _cli_plans(plan)
    if args.dry_run:
        _display(plan, cli_plans, json_output=args.json)
        return 0
    if not (args.yes and args.json):
        _display(plan, cli_plans)
    if not args.yes and not interactive:
        raise Error(
            "Reset requires an interactive terminal or --yes; protected options require explicit flags"
        )
    include_defaults = args.include_default_homes
    uninstall_clis = args.uninstall_clis
    if not args.yes:
        if (
            read(
                "Type RESET to permanently delete the listed Manager data and non-default homes: "
            ).strip()
            != "RESET"
        ):
            print("Reset cancelled.")
            return 0
        defaults = [home for home in plan.homes if home.default]
        if defaults:
            answer = read(
                "Also delete the protected native default homes above? Type DELETE DEFAULT HOMES, or press Enter to keep them: "
            )
            include_defaults = answer.strip() == "DELETE DEFAULT HOMES"
        removable = [row for row in cli_plans if row[1]["automatic"]]
        if removable:
            selected = ", ".join(manager.provider for manager, _ in removable)
            answer = read(
                f"Also uninstall the ownership-verified provider CLI(s) ({selected})? "
                "Type UNINSTALL PROVIDER CLIS, or press Enter to keep them: "
            )
            uninstall_clis = answer.strip() == "UNINSTALL PROVIDER CLIS"
    current = reset.create_plan(
        provider=plan.provider,
        config=config,
        root=home,
        scope=scope,
        reset_provider_config=plan.configuration_reset.requested,
    )
    if current.revision != plan.revision:
        raise Error(
            "Reset plan changed after confirmation; inspect and run the command again"
        )
    cli_results = []
    if uninstall_clis:
        from agent_manager.providers.installation import software

        removable = [row for row in cli_plans if row[1]["automatic"]]
        if not removable:
            raise Error(
                "No ownership-verified provider CLI can be uninstalled automatically"
            )
        for manager, candidate in removable:
            software.validate(manager, "uninstall", candidate["revision"], True)
        for manager, candidate in removable:
            result = software.perform(
                manager,
                "uninstall",
                revision=candidate["revision"],
                confirmed=True,
            )
            cli_results.append(dict(provider=manager.provider, **result))
    result = reset.perform(current, include_default_homes=include_defaults)
    result["providerClis"] = dict(
        requested=uninstall_clis,
        uninstalled=cli_results,
        preserved=[
            row
            for row in _public_cli_plans(cli_plans)
            if row["provider"] not in {item["provider"] for item in cli_results}
        ],
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("Agent Manager reset completed.")
        if result["preservedDefaultHomes"]:
            print("Preserved default native homes:")
            for path in result["preservedDefaultHomes"]:
                print("  " + path)
        if cli_results:
            print("Uninstalled provider CLIs:")
            for row in cli_results:
                print("  " + row["provider"])
        manual = [
            row
            for row in result["providerClis"]["preserved"]
            if not row["automatic"]
        ]
        if manual:
            print("Provider CLIs requiring manual removal were preserved:")
            for row in manual:
                print("  " + row["provider"] + ": " + (row["reason"] or row["path"]))
    return 0
