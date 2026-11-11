"""Profile-only command dispatch."""
import argparse
import json
import sys
from agent_manager.errors import Error
from agent_manager.manager import Manager
from agent_manager.interfaces.cli.parser import Parser, parser


def execute(argv=None, *, progress=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    forwarded = []
    if "--" in argv:
        at = argv.index("--"); forwarded, argv = argv[at+1:], argv[:at]
    routing = Parser(add_help=False)
    routing.add_argument("--config"); routing.add_argument("--home")
    routing.add_argument("command", nargs="?"); routing.add_argument("tail", nargs=argparse.REMAINDER)
    route, unknown = routing.parse_known_args(argv)
    if route.command == 'onboarding' and not unknown:
        if route.tail in (['--help'], ['-h']):
            print('usage: agent [--config FILE] [--home DIR] onboarding\n\nConfigure providers, profiles and sign-in in a terminal UI.')
            return 0
        if route.tail or forwarded: raise Error('agent onboarding does not accept provider arguments')
        from agent_manager.interfaces.cli.onboarding import global_command
        return global_command(config=route.config, home=route.home)
    if route.command == "reset" and not unknown:
        if forwarded: raise Error("Reset does not accept provider arguments")
        from agent_manager.interfaces.cli import reset
        return reset.command(reset.parser(Parser).parse_args(route.tail), config=route.config, home=route.home)
    if route.command in ("install", "uninstall", "update", "versions") and not unknown:
        if forwarded: raise Error("Installation commands do not accept provider arguments")
        if route.command == 'versions':
            from agent_manager.providers.installation import releases
            return releases.command(releases.parser(Parser).parse_args(route.tail))
        if route.command == "install":
            from agent_manager.providers.installation import engine
            return engine.command(engine.parser(Parser).parse_args(route.tail), config=route.config, home=route.home, progress=progress)
        if route.command == "uninstall":
            from agent_manager.providers.installation import uninstall
            return uninstall.command(uninstall.parser(Parser).parse_args(route.tail), config=route.config, home=route.home)
        from agent_manager.providers.installation import updates
        return updates.command(updates.parser(Parser).parse_args(route.tail), config=route.config, home=route.home, progress=progress)
    cli = parser()
    args = cli.parse_args(argv)
    if args.verb == 'update' and forwarded:
        raise Error('Installation commands do not accept provider arguments')
    manager = Manager(args.provider, args.config, args.home)
    if args.verb == 'target':
        if forwarded: raise Error('target does not accept provider arguments')
        result = manager.agent_targets() if args.target_action == 'list' else manager.resolve_agent_target(args.environment_id)
        if args.json: print(json.dumps(result, indent=2))
        else:
            for row in result if isinstance(result, list) else [result]:
                print(f'{row["environment_id"]}  {", ".join(row["aliases"]) or "(no aliases)"}  {row["home"]}')
        return 0
    if args.verb == 'doctor':
        if forwarded: raise Error('doctor does not accept provider arguments')
        result = manager.readiness(args.profile, refresh=args.refresh)
        if args.json: print(json.dumps(result, indent=2))
        else:
            print(f'{manager.provider}: ' + ('ready' if result['ready'] else 'setup required'))
            for issue in result['issues']: print(f'  {issue["code"]}: {issue["message"]}\n    {issue["command"]}')
        return 0
    if args.verb == 'config':
        if args.config_command == 'show':
            if forwarded: raise Error('config show does not accept provider arguments')
            result = manager.configuration(args.profile, effective=args.effective)
            if args.json: print(json.dumps(result, indent=2))
            else:
                import shlex
                print(f'{manager.provider} configuration: {result["path"]}')
                print('Profile: ' + (result['profile'] or '(provider defaults)'))
                print('Effective arguments: ' + (shlex.join(result['arguments']['effective']) or '(none)'))
                print('Model: ' + str(result['native_model'] or result['model'] or 'Native default'))
                for scope in ('provider', 'profile'):
                    if scope in result['instructions']: print(f'{scope.capitalize()} instructions: {result["instructions"][scope]["path"]}')
            return 0
        if args.config_command == 'instructions':
            from agent_manager.interfaces.cli.instructions_config import command
            return command(manager, args, forwarded)
        from agent_manager.providers.saved_args import configure
        result = configure(manager, args.args_action, forwarded, profile=args.profile, expected_revision=getattr(args, 'expected_revision', None))
        if args.json: print(json.dumps(result, indent=2))
        else:
            import shlex
            scope = f'profile "{result["profile"]}"' if result['profile'] is not None else 'all profiles'
            print(f'{manager.provider} launch arguments ({scope}): ' + (shlex.join(result['args']) if result['args'] else '(none)'))
        return 0
    if args.verb == 'onboarding':
        if forwarded: raise Error('Onboarding does not accept forwarded provider arguments')
        from agent_manager.interfaces.cli.onboarding import command
        return command(manager)
    if args.verb == "update":
        from agent_manager.providers.installation import updates
        args.target = args.provider
        return updates.command(args, config=args.config, home=args.home, progress=progress)
    instruction_options = {}
    if args.verb in ("run", "start", "continue"):
        from agent_manager.runtime.context import key_values
        if args.label:
            instruction_options["labels"] = key_values(args.label, "labels")
        if args.metadata:
            instruction_options["metadata"] = key_values(args.metadata, "metadata")
    if args.verb in ("run", "start", "continue"):
        if args.verb == "continue":
            from agent_manager.interfaces.cli.continuation import continue_session
            return continue_session(manager, forwarded, session=args.session, profile=args.profile, model=args.model,
                **instruction_options)
        if args.verb == 'start':
            from agent_manager.interfaces.cli.start import start
            return start(manager, forwarded, model=args.model, **instruction_options)
        return manager._run_native(manager.resolve_profile(args.profile), forwarded, model=args.model, **instruction_options)
    if args.verb == "login": return manager.login(args.profile, forwarded, expected_revision=args.expected_revision)
    if forwarded: raise Error("Forwarded provider arguments require run or login")
    if args.verb == "logout": return manager.logout(args.profile, expected_revision=args.expected_revision)
    if args.verb == 'budget':
        if args.all and args.profile: raise Error('Choose one profile or --all')
        result = (manager.budgets(refresh=args.refresh, cached=args.cached, cwd=args.cwd, model=args.model) if args.all else
                  manager.budget(args.profile, refresh=args.refresh, cached=args.cached, cwd=args.cwd, model=args.model))
        if args.json: print(json.dumps(result, indent=2))
        else:
            from agent_manager.budgets.service import display
            display(result)
        return 0
    if args.verb == "status": result = manager.login_status(args.profile)
    elif args.verb == "list" or args.verb == "profile" and args.action == "list":
        from agent_manager.profiles.listing import list_profiles
        result = list_profiles(manager)
    elif args.verb == "sessions":
        from agent_manager.profiles.sessions import listing
        result = listing(manager)
    elif args.verb == 'environment':
        from agent_manager.environments import operations as environments
        if args.environment_action == 'list': result = environments.listing(manager)
        elif args.environment_action == 'show': result = environments.inspect_environment(manager, args.environment_id)
        else: result = environments.remove(manager, args.environment_id, purge=args.environment_action == 'purge', expected_revision=args.expected_revision)
    elif args.verb == "profile":
        from agent_manager.profiles import operations
        action = args.action
        if action == "alias": result = manager.alias_profile(args.new_name, args.profile, environment_id=args.environment_id)
        elif action == "new": result = manager.new_profile(args.profile, model=args.model)
        elif action == "connect": result = manager.connect_profile(args.profile, args.directory)
        elif action == "show": result = operations.inspect_profile(manager, args.profile)
        elif action == "default": manager.set_default(args.profile, expected_revision=args.expected_revision); result = operations.inspect_profile(manager, args.profile)
        elif action == "rename": result = manager.rename(args.profile, args.new_name, expected_revision=args.expected_revision)
        elif action == "duplicate": result = operations.duplicate_profile(manager, args.profile, args.new_name, expected_revision=args.expected_revision)
        elif action in ("disconnect", "delete"):
            result = operations.delete_profile(manager, args.profile, expected_revision=args.expected_revision)
        elif action == "model":
            if args.model is None and not args.clear: raise Error("Choose a model or --clear")
            manager.set_model(args.profile, None if args.clear else args.model)
            result = operations.inspect_profile(manager, args.profile)
        else: raise Error("Unsupported profile operation")
    elif args.verb == 'integrations':
        result = manager.integrations(args.profile, cwd=args.cwd, refresh=args.refresh, cached=args.cached)
        if not args.json:
            from agent_manager.integrations.service import display
            display(result)
            return 0
    elif args.verb == "models":
        result = manager.models(args.profile, free=args.free, cwd=args.cwd, refresh=args.refresh, cached=args.cached)
        if not args.json:
            from agent_manager.models.service import display
            display(result)
            return 0
    else: raise Error("Unsupported command")
    if (args.verb in ('list', 'status') or args.verb == 'profile') and not getattr(args, 'json', False):
        from agent_manager.interfaces.cli.presentation import display
        display(manager, args, result)
    else:
        print(json.dumps(result, indent=2))
    return 0


def main():
    try: raise SystemExit(execute())
    except KeyboardInterrupt:
        # Terminal SIGINT also reaches the wrapper while it waits for the native
        # CLI. Launch cleanup unwinds before this boundary; cancellation is not
        # a Python error to display to the user.
        raise SystemExit(130) from None
    except (Error, OSError, ValueError) as error:
        print(str(error), file=sys.stderr); raise SystemExit(2)


if __name__ == "__main__": main()
