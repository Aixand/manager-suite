"""Install supported CLI applications independently of profiles and credentials."""
from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid

from agent_manager.errors import Error
from agent_manager.storage.files import atomic
from agent_manager.storage.locks import locks
from agent_manager.interfaces.cli import tui
from agent_manager.providers.installation.progress import InstallationProgress, download


METHODS = {"installer": "Official native installer"}
BUNDLED_NOTE = 'Included with Aixand. Update Aixand to update this CLI; use Disable or Restore to manage its availability.'
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?")


def catalog():
    from agent_manager.providers.terminal_policy import recommended
    entries = tomllib.loads(Path(__file__).with_name("install_catalog.toml").read_text())["agents"]
    for provider, spec in entries.items():
        spec.update(policy_provider=provider, recommended_version=recommended(provider))
    return entries


def supported_methods(spec):
    return spec.get('windows_methods', ['installer']) if platform.system() == 'Windows' else ['installer']


def environment_info():
    """Describe the execution host, never the computer viewing a web client."""
    system = platform.system()
    machine = platform.machine().lower()
    architecture = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)
    wsl = system == "Linux" and ("microsoft" in platform.release().lower() or bool(os.environ.get("WSL_DISTRO_NAME")))
    return {"os": {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(system, "unknown"),
            "label": "Linux (WSL)" if wsl else {"Darwin": "macOS"}.get(system, system),
            "architecture": architecture, "wsl": wsl,
            "tools": [tool for tool in ("curl", "tar", "sh", "bash") if shutil.which(tool)],
            "managedSupported": system in ("Linux", "Darwin", "Windows") and architecture in ("x64", "arm64")}


def setup_guide(provider):
    """Read-only instructions use the same recipes as managed installation."""
    data = tomllib.loads(Path(__file__).with_name("install_catalog.toml").read_text())
    spec = catalog().get(provider)
    environment = environment_info()
    if spec:
        methods = []
        if environment["os"] in ("linux", "macos", "windows"):
            for method in supported_methods(spec):
                label = METHODS[method]
                version = spec['recommended_version']
                if method == 'installer' and spec.get('pinned_artifact'):
                    command = f'agent install {provider} --installer --version {version} --yes'
                elif environment['os'] == 'windows' and method == 'installer':
                    command = f'agent install {provider} --installer --version {version} --yes'
                else:
                    if method == 'installer':
                        arguments = [spec['interpreter'], '-s', '--', *install_args(spec, method, version)[2:]]
                        prefix = f'VERSION={shlex.quote(version)} ' if spec.get('version_environment') else ''
                        command = f"curl -fsSL {spec['installer_url']} | " + prefix + shlex.join(arguments)
                    else:
                        command = shlex.join(install_args(spec, method, version))
                methods.append({"id": method, "label": label, "command": command,
                                "error": prerequisites(spec, method, version)})
        return {"docs": spec["docs"], "verifyCommand": shlex.join([spec['executable'], *version_arguments(spec)]), "methods": methods,
                'recommendedVersion': spec['recommended_version']}
    guide = data.get("guides", {}).get(provider)
    if not guide:
        return None
    command = guide.get("windows_command" if environment["os"] == "windows" else "posix_command") \
        if environment["os"] != "unknown" else None
    return {"docs": guide["docs"], "verifyCommand": guide["executable"] + " --version",
            "methods": [{"id": "manual", "label": "Official installer", "command": command, "error": None}] if command else []}


def parser(parser_type):
    cli = parser_type(
        prog="agent install",
        description="Install a supported provider CLI. Omitted choices open a terminal selector.",
        epilog="""Examples:
  agent install
  agent install codex
  agent install opencode --yes
  agent install codex --installer --yes
  agent install codex --version latest --reinstall --dry-run
  agent versions opencode
  agent install --list

Existing compatible installations are kept unless --reinstall is supplied or
Reinstall is selected. Switching installation methods requires manual migration.
Use recommended (the default), latest approved by this Agent Manager, or an exact
approved release with --version. Global --config and --home select provider context;
software uses the vendor's native user-wide installation location. Only official
native installers are supported; npm and Homebrew are not installation methods.
No login is performed.""",
    )
    cli.add_argument("target", nargs="?", choices=list(catalog()), metavar="PROVIDER")
    methods = cli.add_mutually_exclusive_group()
    methods.add_argument("--method", choices=list(METHODS), help="Installation method")
    for method, label in METHODS.items():
        methods.add_argument("--" + method, dest="method", action="store_const", const=method,
                             help=f"Use {label}")
    cli.add_argument("--yes", "-y", action="store_true", help="Use the recommended available method without a TUI")
    cli.add_argument("--dry-run", action="store_true", help="Print the plan without downloading, installing, or writing state")
    cli.add_argument("--list", action="store_true", help="List supported applications, methods, and installed versions")
    cli.add_argument("--reinstall", action="store_true", help="Explicitly reinstall using the existing method")
    cli.add_argument("--version", metavar="VERSION", help="recommended (default), latest approved by Agent Manager, or an exact approved release")
    return cli


def state_root(root=None):
    from agent_manager._paths import state_root as manager_state_root
    return manager_state_root(root)


def native_binary(spec):
    if not spec.get('native_bin'):
        return None
    if platform.system() == 'Windows':
        from agent_manager.providers.installation.windows import destination
        return destination(spec)
    return Path.home() / spec["native_bin"].removeprefix("~/")


def provider_executable(manager):
    """Resolve the configured CLI, including a vendor's default user install."""
    from agent_manager.providers.installation.exports import managed_path
    managed = managed_path(manager)
    if managed is not None:
        return managed
    executable = manager.config["executable"]
    selected = shutil.which(executable)
    if selected:
        return Path(selected).absolute()
    data = tomllib.loads(Path(__file__).with_name("install_catalog.toml").read_text())
    spec = data["agents"].get(manager.provider) or data.get("guides", {}).get(manager.provider)
    if not spec or executable != spec["executable"] or platform.system() not in ("Linux", "Darwin", "Windows"):
        return None
    paths = [native_binary(spec)]
    if spec.get('npm_package') and (prefix := manager_prefix('npm')):
        paths.append(prefix / (spec['executable'] + '.cmd' if os.name == 'nt' else 'bin/' + spec['executable']))
    return next((path for path in paths if path and path.is_file() and os.access(path, os.X_OK)), None)


def refresh_provider_path(manager):
    """Make a newly installed vendor CLI usable, preserving executable overrides."""
    if shutil.which(manager.config['executable']):
        return
    path = provider_executable(manager)
    if path is not None:
        os.environ['PATH'] = str(path.parent) + os.pathsep + os.environ.get('PATH', '')


def output(args):
    """Bound read-only local probes; failed probes never imply installation."""
    try:
        # Metadata probes must not inherit an application's protocol input. Some
        # CLIs also change the inherited descriptor's blocking mode on startup.
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', errors="replace", timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def package_manager_command(method, args):
    executable = shutil.which(method)
    if not executable:
        raise Error(f'{method} is not available on this machine')
    # Invoke npm's JavaScript entry directly instead of passing user paths to a
    # Windows batch-file shell. Packaged npm.exe launchers are already argv-safe.
    if os.name == 'nt' and Path(executable).suffix.lower() in {'.cmd', '.ps1'}:
        script = Path(executable).parent / 'node_modules/npm/bin/npm-cli.js'
        node = shutil.which('node')
        if method != 'npm' or not script.is_file() or not node:
            raise Error('The package manager has no supported executable entry point. Use manual installation.')
        return [node, str(script), *args]
    return [executable, *args]


def npm_entry(spec, path):
    """Resolve a registered npm CLI without evaluating a Windows batch shim."""
    package = spec.get('npm_package')
    if not package:
        return None
    root = path.parent / 'node_modules' / package
    try:
        metadata = json.loads((root / 'package.json').read_bytes())
        entries = metadata.get('bin', {})
        entry = entries if isinstance(entries, str) else entries.get(spec['executable'])
        candidate = (root / entry).resolve() if entry else None
        if metadata.get('name') == package and candidate and candidate.is_relative_to(root.resolve()) and candidate.is_file():
            return candidate
    except (OSError, ValueError, TypeError):
        pass
    return None


def executable_command(path, args, spec=None):
    path = Path(path)
    if os.name == 'nt' and not path.is_absolute() and (resolved := shutil.which(str(path))):
        path = Path(resolved)
    if os.name != 'nt' or path.suffix.lower() not in ('.cmd', '.ps1'):
        return [str(path), *args]
    spec = spec or next((row for row in catalog().values() if row['executable'].lower() == path.stem.lower()), {})
    from agent_manager.providers.installation.exports import node_for
    entry, node = npm_entry(spec, path), node_for(path) or shutil.which('node')
    if not entry or not node:
        raise Error('This provider has no verified npm executable entry point. Check its installation.')
    return [node, str(entry), *args]


def manager_prefix(method):
    executable = shutil.which(method)
    if not executable:
        return None
    if method == 'npm':
        # npm metadata commands write cache logs even when only reading prefix.
        # An explicit environment prefix already determines the same location;
        # use it without spawning npm or creating a never-started workspace.
        configured = os.environ.get('npm_config_prefix') or os.environ.get('NPM_CONFIG_PREFIX')
        if configured and '\n' not in configured and '\r' not in configured:
            prefix = Path(configured).expanduser()
            if prefix.is_absolute():
                return prefix
    value = output(package_manager_command(method, ["prefix", "-g"] if method == "npm" else ["--prefix"]))
    return Path(value) if value and "\n" not in value and Path(value).is_absolute() else None


@dataclass(frozen=True)
class Installation:
    path: Path
    method: str | None
    version: str | None
    error: str | None

    @property
    def status(self):
        return self.error or f"Installed {self.version} ({self.method or 'unknown method'})"


def identify_method(spec, path):
    from agent_manager.providers.installation.software import bundled
    if bundled(path):
        return 'bundled'
    resolved = path.resolve()
    if spec.get('npm_package') and (f"/node_modules/{spec['npm_package']}/" in resolved.as_posix() or
                                  os.name == 'nt' and path.suffix.lower() in ('.cmd', '.ps1') and npm_entry(spec, path)):
        return "npm"
    for directory in ("Cellar", "Caskroom"):
        if directory in resolved.parts:
            index = resolved.parts.index(directory) + 1
            if spec.get('brew_package') and resolved.parts[index:index + 1] == (spec["brew_package"].rsplit("/", 1)[-1],):
                return "brew"
    if path == native_binary(spec):
        # A symlink placed here by a different package manager is not native.
        if not path.is_symlink() or (spec["executable"] == "codex" and
                                    resolved.is_relative_to(Path.home() / ".codex/packages/standalone")) or (
                                    spec['executable'] == 'claude' and resolved.is_relative_to(Path.home() / '.local/share/claude')):
            return "installer"
    return None


def version_from_output(value):
    # CLI banners can end a version with sentence punctuation (Copilot does).
    # Remove only a sentence-ending period; dotted numeric versions still have
    # to match the complete three-component version rather than a prefix.
    value = re.sub(r"(?<=\w)\.(?=\s|$)", "", value or "")
    match = re.search(r"(?<![\w.])v?(" + VERSION.pattern + r")(?![\w.])", value)
    return match.group(1) if match else None


def version_arguments(spec):
    """Inspect the installed payload using the recipe's native version probe."""
    return spec.get('version_args', ['--version'])


def inspect(spec, path):
    try:
        value = output(executable_command(path, version_arguments(spec), spec))
    except Error:
        value = None
    version = version_from_output(value)
    if not version or (spec["executable"] == "codex" and "codex" not in (value or "").lower()):
        error = "Version check failed"
    elif spec.get("compatible_major") and int(version.split(".")[0]) != spec["compatible_major"]:
        error = f"Incompatible {version}; requires {spec['label']}.x"
    else:
        error = None
    return Installation(path, identify_method(spec, path), version, error)


def inspect_external(manager):
    """Read configured CLI metadata even when no managed install recipe exists.

    Detection never grants permission to install or update the executable.
    Unrecognized layouts deliberately keep an unknown method.
    """
    executable = manager.config["executable"]
    path = provider_executable(manager)
    if path is None:
        return None
    spec = catalog().get(manager.provider, {'executable': executable})
    try:
        value = output(executable_command(path, version_arguments(spec), spec))
    except Error:
        value = None
    version = version_from_output(value)
    resolved, method = path.resolve(), None
    from agent_manager.providers.installation.software import bundled
    if bundled(path):
        method = 'bundled'
    elif "node_modules" in resolved.parts:
        method = "npm"
    elif any(directory in resolved.parts for directory in ("Cellar", "Caskroom")):
        method = "brew"
    elif manager.provider == "claude" and resolved.is_relative_to(Path.home() / ".local/share/claude/versions"):
        method = "installer"
    elif path == Path.home() / ".local/bin" / Path(executable).name and not path.is_symlink():
        method = "standalone"
    return Installation(path, method, version, None if version else "CLI version check failed.")


def discover(spec):
    name = spec["executable"]
    directories = os.environ.get("PATH", "").split(os.pathsep)
    if os.name == 'nt':
        # npm also creates an extensionless Unix shell launcher on Windows.
        # Probe each PATH directory using Windows executable rules so that the
        # shell launcher is not mistaken for another installed CLI.
        candidates = [Path(selected).absolute() for directory in directories
                      if (selected := shutil.which(name, path=directory or '.'))]
    else:
        candidates = [Path(directory or '.').absolute() / name for directory in directories]
    candidates.append(native_binary(spec))
    for method in ("npm", "brew"):
        # Existing package-manager CLIs are observable, never managed installers.
        if not spec.get('npm_package' if method == 'npm' else 'brew_package'):
            continue
        prefix = manager_prefix(method)
        if prefix:
            candidates.append(prefix / (name + '.cmd' if os.name == 'nt' and method == 'npm' else 'bin/' + name))
    seen, found = set(), []
    for path in candidates:
        if path is None:
            continue
        from agent_manager.providers.installation.exports import canonical_path
        path = canonical_path(spec, path)
        try:
            if not path.is_file() or not os.access(path, os.X_OK):
                continue
            resolved = path.resolve()
        except (OSError, RuntimeError):
            # A broken or inaccessible PATH entry is not an installation.
            # Windows App Execution Aliases can be untrusted mount points.
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        found.append(inspect(spec, path))
    return found


def method_support(spec, method, version=None):
    """Recipe/platform eligibility, without starting a CLI or checking the network."""
    if method != 'installer':
        return 'Only official native installers are supported; npm and Homebrew installation are unavailable.'
    if method not in supported_methods(spec):
        return 'This provider does not support this installation method on this machine.'
    system = platform.system()
    if system not in ('Linux', 'Darwin', 'Windows'):
        return 'Supported on Linux, macOS and Windows only'
    if platform.machine().lower() not in ('x86_64', 'amd64', 'arm64', 'aarch64'):
        return 'Requires an x64 or ARM64 machine'
    if spec.get('pinned_artifact') and method == 'installer':
        from agent_manager.providers.policy import artifact
        try:
            artifact(spec['policy_provider'], system, platform.machine(), version)
        except (KeyError, ValueError) as error:
            return str(error)
        return None
    if system == 'Windows':
        from agent_manager.providers.installation.windows import prerequisites as windows_prerequisites
        return windows_prerequisites(spec, method, version)
    return None


def prerequisites(spec, method, version=None):
    version = version or spec.get('recommended_version')
    problem = method_support(spec, method, version)
    if problem: return problem
    if spec.get('pinned_artifact') and method == 'installer' or platform.system() == 'Windows':
        return None
    required = [spec["interpreter"], "curl", "tar"]
    missing = [tool for tool in required if not shutil.which(tool)]
    if missing:
        return "Missing: " + ", ".join(missing)
    return None


def migration_error(found, method):
    if any(item.method == 'bundled' for item in found):
        return BUNDLED_NOTE
    if any(item.method != method for item in found):
        return "Existing installation uses another or unknown method; migrate it manually before using " + method
    if len(found) > 1:
        return "Multiple installations found; resolve PATH conflicts before reinstalling"
    return None


def validate_version(spec, version):
    from agent_manager.providers.installation.releases import exact
    return exact(spec['policy_provider'], version)


def install_args(spec, method, version=None, *, update=False):
    if method != 'installer':
        raise Error('Only official native installers are supported.')
    version = version or spec.get('recommended_version')
    if not version:
        raise Error('Choose an exact provider release before installation.')
    version = validate_version(spec, version)
    args = [spec["interpreter"], "<downloaded-installer>"]
    if version and not spec.get('version_environment'):
        args += ([spec["version_flag"]] if spec.get('version_flag') else []) + [version]
    if spec["executable"] == "opencode":
        args.append("--no-modify-path")
    return args


def destination(spec, method):
    if method != 'installer':
        raise Error('Only official native installers are supported.')
    return native_binary(spec)


@contextmanager
def native_replacement(expected, replace=False):
    """Restore the previous executable if its replacement fails verification."""
    backup = None
    if replace and expected.is_file():
        backup = expected.with_name(expected.name + '.agent-backup-' + uuid.uuid4().hex)
        expected.replace(backup)
    try:
        yield
    except BaseException:
        if backup:
            backup.replace(expected)
        raise
    else:
        if backup:
            backup.unlink()




def print_found(found):
    for item in found:
        print(f"  {item.path}: {item.status}")


def report_path(spec, path):
    selected = shutil.which(spec["executable"])
    if not selected:
        print(f"Add {path.parent} to PATH, then open a new terminal.")
    elif Path(selected).resolve() != path.resolve():
        print(f"PATH currently selects {selected}; put {path.parent} first to use this installation.")


def run_checked(args, **kwargs):
    result = subprocess.run(args, **kwargs)
    if result.returncode:
        raise Error(f"{Path(args[0]).name} failed (exit {result.returncode}); see the installer output above. "
                    "The installation may be incomplete; no success receipt was written.")


def perform(target, spec, method, version, reinstall, yes, *, update_check=None, progress=None, installation_plan=None, root=None):
    return _perform(target, spec, method, version, reinstall, yes, update_check=update_check,
                    progress=progress, installation_plan=installation_plan, root=root)


def _perform(target, spec, method, version, reinstall, yes, *, update_check=None, progress=None, installation_plan=None, root=None):
    from agent_manager.providers.installation.releases import for_install
    version = for_install(target, version, method=method, plan=installation_plan)
    root = state_root(root)
    if spec.get('pinned_artifact') and method == 'installer':
        from agent_manager.providers.installation.artifacts import perform
        return perform(target, spec, version, reinstall, update_check=update_check,
                       progress=progress, installation_plan=installation_plan, root=root)
    if platform.system() == 'Windows' and method == 'installer':
        from agent_manager.providers.installation.windows import perform as windows_perform
        return windows_perform(target, spec, method, version, reinstall, yes,
                               update_check=update_check, progress=progress,
                               installation_plan=installation_plan, root=root)
    reporter = InstallationProgress(progress, method)
    reporter.report('preparing')
    with locks(root / "locks", "install-" + target):
        # Recheck under the user-wide lock, after any time spent in the picker.
        found = discover(spec)
        before = update_check(found) if update_check else None
        problem = migration_error(found, method)
        if problem:
            raise Error(problem)
        if found and not reinstall:
            if found[0].error:
                raise Error(found[0].error + "; use --reinstall to repair this installation")
            if version and version != found[0].version:
                raise Error("A different version is installed; use --reinstall to change it")
            print("Keeping existing installation.")
            print_found(found)
            report_path(spec, found[0].path)
            reporter.report('complete', existing=True)
            return 0
        problem = prerequisites(spec, method, version)
        if problem:
            raise Error(problem)
        expected = destination(spec, method)
        # Do not overwrite a non-executable file, directory, or broken symlink.
        if os.path.lexists(expected) and not any(item.path.resolve() == expected.resolve() for item in found):
            raise Error(f"Installation destination is occupied: {expected}; resolve it manually first")
        args = install_args(spec, method, version, update=bool(update_check))
        args = [shutil.which(args[0]), *args[1:]]
        env = os.environ.copy()
        with native_replacement(expected, bool(found)), tempfile.TemporaryDirectory(prefix="agent-install-") as temporary:
            if method == "installer":
                script = Path(temporary) / "install.sh"
                download([shutil.which("curl"), "--fail", "--silent", "--show-error", "--location",
                             "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "15",
                             "--max-time", "120", "--output", str(script), spec["installer_url"]], script, reporter, cwd=temporary)
                if not script.is_file() or not script.read_bytes().startswith(b"#!"):
                    raise Error("Downloaded installer is empty or is not a shell script; nothing was executed")
                args[1] = str(script)
                env["PATH"] = str(expected.parent) + os.pathsep + env.get("PATH", "")
                env.setdefault("SHELL", "/bin/sh")
                if target == "codex":
                    env.update(CODEX_HOME=str(Path.home() / ".codex"), CODEX_INSTALL_DIR=str(expected.parent),
                               CODEX_RELEASE=version, CODEX_INSTALL_DAEMON_ONLY="0",
                               CODEX_INSTALL_DEFER_SELECTION="0", CODEX_NON_INTERACTIVE="1" if yes else "0")
                else:
                    env["VERSION"] = version or ""
                if target == 'copilot':
                    env['PREFIX'] = str(expected.parent.parent)
            reporter.report('installing')
            run_checked(args, cwd=temporary, env=env)
            # Verify before releasing a previous native executable's backup.
            reporter.report('verifying')
            if not expected.is_file() or not os.access(expected, os.X_OK):
                raise Error(f"Installer completed, but no executable was found at {expected}")
            installed = inspect(spec, expected)
            if installed.error:
                raise Error(f"Installed executable failed verification: {installed.error} ({expected})")
            if version and installed.version != version:
                raise Error(f"Requested {version}, but {expected} reports {installed.version}")
        receipt = {"target": target, "method": method, "path": str(expected), "version": installed.version,
                   "installed_at": datetime.now(timezone.utc).isoformat(), "docs": spec["docs"]}
        from agent_manager.providers.installation.plans import receipt_metadata
        receipt.update(receipt_metadata(version, installation_plan))
        if before:
            receipt["updated_at"] = receipt["installed_at"]
            receipt["previous_version"] = before.version
        atomic(root / "installations" / (target + ".json"), (json.dumps(receipt, indent=2) + "\n").encode())
        if before:
            change = f"{before.version} -> {installed.version}" if before.version != installed.version else f"{installed.version} (version unchanged)"
            print(f"Update completed for {spec['label']}: {change}; {expected}")
        else:
            print(f"Installed {spec['label']} {installed.version}: {expected}")
        report_path(spec, expected)
        print(f"Next: agent {target} run")
        reporter.report('complete')
        return 0


def command(args, *, picker=None, progress=None, config=None, home=None):
    picker = picker or tui.pick
    reporter = InstallationProgress(progress, args.method)
    if not args.list and not args.dry_run:
        reporter.report('preparing')
    entries = catalog()
    if args.list:
        if args.reinstall or args.version or args.dry_run:
            raise Error("--list cannot be combined with --reinstall, --version, or --dry-run")
        for target, spec in entries.items():
            if args.target and target != args.target:
                continue
            print(f"{target}: {spec['label']}")
            found = discover(spec)
            print_found(found)
            if not found:
                print("  Not installed")
            for method in METHODS:
                if not args.method or method == args.method:
                    print(f"  {method}: {prerequisites(spec, method) or 'Available'}")
        return 0
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    target = args.target
    if target is None:
        if args.yes or args.dry_run or not interactive:
            raise Error("Specify a provider, for example: agent install codex --yes")
        names = list(entries)
        rows = []
        disabled = []
        for index, name in enumerate(names):
            spec = entries[name]
            found = discover(spec)
            status = found[0].status if found else "Not installed"
            problem = prerequisites(spec, args.method) if args.method else None
            if problem:
                disabled.append(index)
            rows.append((spec["label"], status, problem or (str(found[0].path) if found else "Ready to install")))
        selected = picker("Install an agent", ["Application", "Status", "Details"], rows,
                          "Choose an application; Esc/q cancels.", disabled=disabled)
        if selected is None:
            return 0
        target = names[selected]
    from agent_manager.providers.installation import plans, releases
    from agent_manager.providers.policy import compatibility
    from agent_manager.manager import Manager
    spec = entries[target]
    selection = releases.selector(args.version)
    found = discover(spec)
    version = releases.resolve(target, selection) if selection is not None else None
    method, reinstall = args.method, args.reinstall
    if found: print_found(found)
    if method is None and not args.yes and not args.dry_run:
        if not interactive:
            raise Error("Use --yes for a non-interactive native installation")
        choices, rows, disabled = [], [], []
        if found and not found[0].error and compatibility(target, found[0].version)['compatible'] and (version is None or found[0].version == version) and not reinstall:
            choices.append(None)
            rows.append(("Keep installed version", found[0].version, str(found[0].path)))
        for candidate in supported_methods(spec):
            problem = prerequisites(spec, candidate, version) or migration_error(found, candidate)
            if problem: disabled.append(len(rows))
            choices.append(candidate)
            rows.append((METHODS[candidate], "Unavailable" if problem else "Recommended" if candidate == (found[0].method if found else 'installer') else "Available",
                         problem or ("Reinstall existing application" if found else "Install application")))
        selected = picker(f"Install {spec['label']}", ["Method", "Status", "Details"], rows,
                          "Enter chooses this action; Esc/q cancels.", disabled=disabled,
                          initial=next((i for i in range(len(rows)) if i not in disabled), 0))
        if selected is None: return 0
        method = choices[selected]
        if method is not None: reinstall = bool(found) or reinstall
    manager = Manager(target, config=config, root=home)
    plan = plans.resolve(manager, 'install', method=method, version_target=selection, reinstall=reinstall, allow_unavailable=args.dry_run)
    preview(spec, plan, dry_run=args.dry_run)
    if args.dry_run: return 0
    return plans.execute(manager, plan, yes=args.yes or not interactive, progress=progress)


def preview(spec, plan, *, dry_run=False, update=False):
    """Describe the exact shared plan; execution never receives a moving selector."""
    from agent_manager.providers.installation import plans
    plans.describe(plan)
    if plan['keepExisting']: return
    method, version = plan['method'], plan['targetVersion']
    print(f"{'Would install' if dry_run else 'Installing'} {spec['label']} using {METHODS.get(method, method)}.")
    if not method: return
    planned = install_args(spec, method, version, update=update) if method in supported_methods(spec) else [method, '<unavailable>']
    if method == 'installer' and spec.get('pinned_artifact'):
        from agent_manager.providers.policy import artifact
        if not plan['blockedReason']:
            source = artifact(plan['provider'], platform.system(), platform.machine(), version)
            print('Download: ' + source['url']); print('SHA512: ' + source['sha512'])
            print('Action: Verify checksum and exact CLI version before installation.')
    elif method == 'installer' and platform.system() == 'Windows' and method in supported_methods(spec):
        from agent_manager.providers.installation.windows import URLS, opencode_download
        source = URLS.get(spec['executable']) or opencode_download(None, version, None)
        print('Download: ' + source)
        print('Action: Verify and install the standalone CLI ZIP.' if plan['provider'] == 'opencode' else 'Action: Run the official Windows PowerShell installer.')
    else:
        if method == 'installer' and spec.get('installer_url'): print('Download: ' + spec['installer_url'])
        print(('Update: ' if update else 'Command: ') + shlex.join(planned))
