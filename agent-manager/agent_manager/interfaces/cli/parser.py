"""CLI parser definitions and help text."""
import argparse
from agent_manager._version import __version__


class HelpFormatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog):
        super().__init__(prog, max_help_position=26, width=88)

    def _format_action(self, action):
        # Show the command descriptions without argparse's redundant COMMAND row.
        if isinstance(action, argparse._SubParsersAction):
            return "".join(self._format_action(child) for child in action._get_subactions())
        return super()._format_action(action)


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("formatter_class", HelpFormatter)
        super().__init__(*args, **kwargs)
        self._positionals.title = "Arguments"
        self._optionals.title = "Options"


def parser():
    root = Parser(prog="agent", description="Manage provider-native profiles and sessions.",
                  epilog="Global commands: agent reset. Software commands: agent install PROVIDER, agent uninstall PROVIDER, agent update PROVIDER, agent versions PROVIDER. Use --help after each command for options.")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--config", metavar="FILE")
    root.add_argument("--home", metavar="DIR", help="State root (default: $AIXAND_MANAGER_ROOT/agent/state or $XDG_DATA_HOME/aixand/manager/agent/state)")
    root.add_argument("provider", metavar="PROVIDER")
    commands = root.add_subparsers(dest="verb", required=True)
    targets = commands.add_parser('target', help='Discover stable environments for independent tools')
    target_commands = targets.add_subparsers(dest='target_action', required=True)
    target_commands.add_parser('list').add_argument('--json', action='store_true')
    target_show = target_commands.add_parser('show')
    target_show.add_argument('environment_id')
    target_show.add_argument('--json', action='store_true')
    commands.add_parser('onboarding', help='Configure installation, profiles and sign-in interactively')
    doctor = commands.add_parser('doctor', help='Inspect setup, sign-in and qualified provider features')
    doctor.add_argument('--profile')
    doctor.add_argument('--refresh', action='store_true', help='Query native sign-in and account allowance')
    doctor.add_argument('--json', action='store_true')
    configuration = commands.add_parser('config', help='Manage saved launch arguments and native instructions')
    config_commands = configuration.add_subparsers(dest='config_command', required=True)
    config_show = config_commands.add_parser('show', help='Inspect configuration and its sources')
    config_show.add_argument('--profile')
    config_show.add_argument('--effective', action='store_true', help='Include resolved argument and model defaults')
    config_show.add_argument('--json', action='store_true')
    arguments = config_commands.add_parser('args', help='Literal arguments passed to native launches')
    argument_commands = arguments.add_subparsers(dest='args_action', required=True)
    for action in ('add', 'remove', 'list', 'clear'):
        item = argument_commands.add_parser(action, help=f'{action.capitalize()} saved launch arguments')
        item.add_argument('--profile', help='Scope to a profile native environment; aliases share arguments')
        item.add_argument('--json', action='store_true')
        if action != 'list': item.add_argument('--expected-revision')
    instruction_config = config_commands.add_parser('instructions', help='Edit native Markdown instruction files')
    instruction_commands = instruction_config.add_subparsers(dest='instructions_action', required=True)
    for action in ('edit', 'show', 'path'):
        description = {'edit': 'Edit or create native instructions', 'show': 'Show instruction file contents', 'path': 'Print the instruction file path'}[action]
        item = instruction_commands.add_parser(action, help=description)
        item.add_argument('--profile', help='Edit this profile native file; otherwise use the default profile')
        item.add_argument('--json', action='store_true')
        if action == 'edit': item.add_argument('--expected-revision')
    run = commands.add_parser("run", help="Launch a profile using its native home")
    run.add_argument("profile", help="Profile to launch")
    automatic = commands.add_parser("start", help="Select the profile with the most remaining usable allowance")
    selection = commands.add_parser("continue", help="Choose a native session and execution profile")
    selection.add_argument("session", nargs="?", help="Native session ID or full Manager session reference")
    selection.add_argument("--profile", help="Execution profile; otherwise choose interactively")
    for item in (run, automatic, selection):
        item.add_argument("--model")
        item.add_argument("--label", action="append", default=[])
        item.add_argument("--metadata", action="append", default=[])
    for action in ("login", "status", "logout"):
        item = commands.add_parser(action, help=f"Native profile {action}")
        item.add_argument("profile", nargs="?")
        if action != "status": item.add_argument("--expected-revision", help="Expected environment revision")
    commands.add_parser("sessions", help="List durable references to native sessions").add_argument("--json", action="store_true")
    commands.add_parser("list", help="List connected profiles").add_argument("--json", action="store_true")
    budget = commands.add_parser('budget', help='Inspect account allowance using the selected native environment')
    budget.add_argument('profile', nargs='?')
    budget.add_argument('--all', action='store_true', help='One observation per environment; aliases share a result')
    budget_mode = budget.add_mutually_exclusive_group()
    budget_mode.add_argument('--refresh', action='store_true', help='Query the provider instead of reusing a recent observation')
    budget_mode.add_argument('--cached', action='store_true', help='Read only Manager observations without starting the provider')
    budget.add_argument('--json', action='store_true')
    budget.add_argument('--cwd', help='Project directory for integration-scoped allowances')
    budget.add_argument('--model', help='Model whose paying integration to inspect')
    profile = commands.add_parser("profile", help="Manage native profile homes")
    profile_actions = profile.add_subparsers(dest="action", required=True)
    profile_actions.add_parser("list").add_argument("--json", action="store_true")
    new = profile_actions.add_parser("new", help="Create a fresh profile home")
    new.add_argument("profile"); new.add_argument("--model"); new.add_argument('--json', action='store_true')
    connect = profile_actions.add_parser("connect", help="Connect an existing native home without copying it")
    connect.add_argument("profile"); connect.add_argument("directory"); connect.add_argument('--json', action='store_true')
    for action in ("show", "default", "rename", "duplicate", "disconnect", "delete"):
        item = profile_actions.add_parser(action)
        item.add_argument("profile")
        if action != "show": item.add_argument("--expected-revision", help="Expected alias revision")
        if action in ("rename", "duplicate"): item.add_argument("new_name")
        item.add_argument("--json", action="store_true")
    alias = profile_actions.add_parser('alias', help='Add a name for an existing environment')
    alias.add_argument('new_name')
    alias_source = alias.add_mutually_exclusive_group(required=True)
    alias_source.add_argument('profile', nargs='?')
    alias_source.add_argument('--environment', dest='environment_id')
    alias.add_argument('--json', action='store_true')
    environment = commands.add_parser('environment', help='Inspect or retire native environments')
    environment_actions = environment.add_subparsers(dest='environment_action', required=True)
    environment_actions.add_parser('list').add_argument('--json', action='store_true')
    for action in ('show', 'forget', 'purge'):
        item = environment_actions.add_parser(action)
        item.add_argument('environment_id')
        if action != 'show': item.add_argument('--expected-revision')
        item.add_argument('--json', action='store_true')
    model = profile_actions.add_parser("model"); model.add_argument("profile"); model.add_argument("model", nargs="?")
    model.add_argument("--clear", action="store_true")
    model.add_argument('--json', action='store_true')
    models = commands.add_parser("models", help="Inspect the native model catalog (cached for 24 hours)")
    models.add_argument("action", nargs="?", choices=["list"], default="list")
    models.add_argument("--profile", default=None, help="Profile whose configuration and auth to use")
    models.add_argument("--free", action="store_true", help="Only models with zero advertised token costs")
    models.add_argument("--cwd", help="Project directory for model discovery (default: current directory)")
    models.add_argument("--json", action="store_true")
    model_cache = models.add_mutually_exclusive_group()
    model_cache.add_argument('--refresh', action='store_true', help='Force native model discovery')
    model_cache.add_argument('--cached', action='store_true', help='Read local metadata without starting the CLI')
    integrations = commands.add_parser('integrations', help='Inspect native model integrations and reviewed budget capabilities')
    integrations.add_argument('--profile')
    integrations.add_argument('--cwd')
    integrations.add_argument('--json', action='store_true')
    integration_cache = integrations.add_mutually_exclusive_group()
    integration_cache.add_argument('--refresh', action='store_true')
    integration_cache.add_argument('--cached', action='store_true')
    from agent_manager.providers.installation import updates
    updates.options(commands.add_parser("update", help="Update this provider CLI"))
    return root
