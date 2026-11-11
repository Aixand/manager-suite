"""Update configured CLI applications through their existing installation method."""
from dataclasses import dataclass
from pathlib import Path
import shlex
import shutil
import sys

from agent_manager.providers.installation import engine as install
from agent_manager.interfaces.cli import tui
from agent_manager.errors import Error
from agent_manager.manager import Manager


def options(cli):
    cli.add_argument("--yes", "-y", action="store_true", help="Run the updater without interactive prompts")
    cli.add_argument("--dry-run", action="store_true", help="Print the update plan without downloading or writing state")
    cli.add_argument("--list", action="store_true", help="Show configured providers and their update status")
    cli.add_argument('--version', help='recommended (default), latest approved by Agent Manager, or an exact approved release')


def parser(parser_type):
    cli = parser_type(
        prog="agent update",
        description="Update an installed provider CLI using its existing installation method.",
        epilog="""Examples:
  agent update                       Choose an installed provider
  agent codex update                 Update Codex directly
  agent opencode update --dry-run     Inspect the update command
  agent update codex --yes            Update without a selector or prompts
  agent update --list                 Inspect update eligibility

The selector uses your provider configuration, including executable overrides.
Updates select the Agent Manager recommendation. A newer compatible installation is
kept. Managed updates use the official versioned native installer.
Missing agents must first be installed with agent install.""",
    )
    cli.add_argument("target", nargs="?", choices=list(install.catalog()), metavar="PROVIDER")
    options(cli)
    return cli


@dataclass(frozen=True)
class Candidate:
    target: str
    executable: str
    installation: install.Installation | None
    resolved: Path | None
    error: str | None


def candidate(target, spec, config, home, *, found=None):
    manager = Manager(target, config, home)
    executable = manager.config["executable"]
    found = install.discover(spec) if found is None else found
    selected = install.provider_executable(manager)
    if selected:
        path = Path(selected).absolute()
        known = next((item for item in found if item.path.resolve() == path.resolve()), None)
        item = (install.Installation(path, known.method, known.version, known.error)
                if known else install.inspect(spec, path))
        if not known:
            found = [*found, item]
    elif executable == spec["executable"] and found:
        # Include default native installations even before their directory is on PATH.
        item = found[0]
    else:
        return Candidate(target, executable, None, None,
                         f"Configured executable is not installed: {executable}; use agent install {target} or fix the provider configuration")
    if item.method == 'bundled':
        return Candidate(target, executable, item, item.path.resolve(), install.BUNDLED_NOTE)
    problem = item.error
    if not problem and not item.method:
        problem = "Unknown installation method; update this executable manually"
    if not problem and item.method != 'installer':
        problem = 'This CLI was installed externally. Agent Manager only manages native installations; migrate it manually first.'
    if not problem:
        problem = install.migration_error(found, item.method) or install.prerequisites(spec, item.method)
    if not problem:
        try:
            expected = install.destination(spec, item.method)
        except Error as error:
            problem = str(error)
        else:
            # Avoid updating a different npm prefix, or a fixed path to an old keg.
            if item.path.absolute() != expected.absolute():
                problem = f"Configured executable {item.path} is not the managed entry point {expected}; update it manually or fix the configuration"
    return Candidate(target, executable, item, item.path.resolve(), problem)


def command(args, *, config=None, home=None, picker=None, progress=None):
    if not args.list and not args.dry_run:
        install.InstallationProgress(progress).report('preparing')
    entries = install.catalog()
    target = args.target
    if target is not None and target not in entries:
        raise Error("This provider has no supported update recipe")
    if args.list and args.dry_run:
        raise Error("--list cannot be combined with --dry-run")
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if target is None and not args.list and (args.yes or args.dry_run or not interactive):
        raise Error("Specify a provider, for example: agent codex update --yes; use agent update --list to inspect providers")
    candidates = [candidate(name, spec, config, home) for name, spec in entries.items()
                  if target is None or name == target]
    if args.list:
        for item in candidates:
            print(f"{item.target}: {entries[item.target]['label']}")
            if item.installation:
                install.print_found([item.installation])
            print("  " + (item.error or "Ready to update"))
        return 0
    if target is None:
        candidates = [item for item in candidates if item.installation is not None]
        if not candidates:
            raise Error("No supported configured providers are installed; run agent install first")
        rows = [(entries[item.target]["label"], item.installation.version or "Unknown",
                 install.METHODS.get(item.installation.method, "Unknown"), item.error or "Ready")
                for item in candidates]
        disabled = {index for index, item in enumerate(candidates) if item.error}
        selected = (picker or tui.pick)(
            "Update an agent", ["Provider", "Version", "Method", "Status"], rows,
            "Enter updates the selected provider using its current installation method.",
            disabled=disabled, initial=next((i for i in range(len(rows)) if i not in disabled), 0),
        )
        if selected is None:
            return 0
        chosen = candidates[selected]
    else:
        chosen = candidates[0]
    target, spec, current = chosen.target, entries[chosen.target], chosen.installation
    from agent_manager.providers.installation import plans
    manager = Manager(target, config=config, root=home)
    plan = plans.resolve(manager, 'update', version_target=args.version, candidate=chosen, allow_unavailable=args.dry_run)
    install.preview(spec, plan, dry_run=args.dry_run, update=True)
    if args.dry_run: return 0
    return plans.execute(manager, plan, yes=args.yes or not interactive, progress=progress)
