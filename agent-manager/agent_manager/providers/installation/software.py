"""Provider enablement and ownership-checked CLI removal, shared by all clients."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import uuid

from agent_manager.errors import Error
from agent_manager.storage.files import atomic
from agent_manager.storage.locks import locks

DOCS = {'codex': 'https://learn.chatgpt.com/docs/codex/cli',
        'opencode': 'https://opencode.ai/v2/docs',
        'claude': 'https://code.claude.com/docs/en/setup#uninstall-claude-code',
        'antigravity': 'https://antigravity.google/docs/getting-started?tab=cli',
        'copilot': 'https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli'}


def state_path(manager):
    return manager.root / 'software.json'


def disabled(manager):
    try:
        return json.loads(state_path(manager).read_bytes()).get('disabled') is True
    except FileNotFoundError:
        return False


def require_enabled(manager):
    if disabled(manager):
        raise Error('This provider is disabled. Enable it in Agents → Providers before starting an agent.')


def bundled(path):
    root = os.environ.get('AIXAND_PROVIDER_BUNDLE_ROOT')
    if not root or path is None:
        return False
    return Path(path).absolute().is_relative_to(Path(root).absolute())


def quote(value):
    return "'" + str(value).replace("'", "''") + "'" if os.name == 'nt' else shlex.quote(str(value))


def fingerprint(path):
    try:
        info = path.lstat()
        target = path.stat()
        return [str(path.absolute()), str(path.resolve()), info.st_ino, info.st_size, info.st_mtime_ns,
                target.st_ino, target.st_size, target.st_mtime_ns]
    except FileNotFoundError:
        return [str(path.absolute()), None]


def native_targets(manager, path):
    """Only vendor-owned software locations; never provider settings or homes."""
    from agent_manager.providers.installation import engine as install
    if not install.catalog().get(manager.provider, {}).get('native_bin'):
        return []
    home = Path.home()
    windows = platform.system() == 'Windows'
    suffix = '.exe' if windows else ''
    executable = {'antigravity': 'agy'}.get(manager.provider, manager.provider) + suffix
    locations = [home / '.local/bin' / executable]
    if manager.provider == 'opencode':
        locations = [home / '.opencode/bin' / executable]
    if windows and manager.provider == 'codex':
        locations = [Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local')) /
                     'Programs/OpenAI/Codex/bin/codex.exe']
    if windows and manager.provider == 'antigravity':
        locations = [Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local')) / 'agy/bin/agy.exe']
    if path.absolute() not in [item.absolute() for item in locations]:
        return []
    if any(parent.is_symlink() or getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400
           for parent in path.parents if parent.exists()):
        return []
    # A different manager's symlink does not make its target ours to remove.
    roots = {'codex': home / '.codex/packages/standalone',
             'claude': home / '.local/share/claude'}
    if path.is_symlink():
        if manager.provider not in roots or not path.resolve().is_relative_to(roots[manager.provider]):
            return []
    targets = [path]
    if manager.provider == 'codex' and roots['codex'].exists():
        packages = roots['codex']
        if packages.is_symlink() or getattr(packages.lstat(), 'st_file_attributes', 0) & 0x400:
            return []
        targets.append(packages)
    if manager.provider == 'claude' and (home / '.local/share/claude').exists():
        versions = home / '.local/share/claude'
        if versions.is_symlink() or getattr(versions.lstat(), 'st_file_attributes', 0) & 0x400:
            return []
        targets.append(home / '.local/share/claude')
    # Codex's Windows install directory contains the CLI and its native helpers.
    if windows and manager.provider == 'codex':
        targets = [path.parent.parent]
    return targets


def package_removal(manager, path, method):
    """Verify the selected package manager owns the exact detected CLI."""
    from agent_manager.providers.installation import engine as install
    spec = install.catalog().get(manager.provider, {})
    prefix = install.manager_prefix(method)
    if prefix is None: return []
    prefix = Path(prefix).resolve()
    resolved = path.resolve()
    if method == 'npm':
        package = spec.get('npm_package')
        if not package: return []
        root = prefix / ('node_modules' if os.name == 'nt' else 'lib/node_modules') / package
        try:
            metadata = json.loads((root / 'package.json').read_text())
        except (OSError, ValueError): return []
        if not isinstance(metadata, dict): return []
        entries = metadata.get('bin', {})
        relative = entries if isinstance(entries, str) else entries.get(spec['executable']) if isinstance(entries, dict) else None
        if not isinstance(relative, str): return []
        entry = (root / relative).resolve()
        if (metadata.get('name') != package or entry is None
                or not entry.is_relative_to(root.resolve()) or not entry.is_file()):
            return []
        # Unix executable must itself resolve into this registered package.
        if os.name != 'nt' and resolved != entry: return []
        if os.name == 'nt' and install.npm_entry(spec, path) != entry: return []
        return install.package_manager_command('npm', ['uninstall', '--global', '--prefix', str(prefix), package])
    package = spec.get('brew_package')
    directory = 'Caskroom' if spec.get('brew_cask') else 'Cellar'
    if not package or not resolved.is_relative_to(prefix / directory / package.rsplit('/', 1)[-1]): return []
    return install.package_manager_command('brew', ['uninstall', '--cask' if spec.get('brew_cask') else '--formula', package])


def plan(manager, found=None):
    from agent_manager.providers.installation import engine as install
    if found is None:
        spec = install.catalog().get(manager.provider)
        path = install.provider_executable(manager)
        found = (install.inspect(spec, path) if spec else install.inspect_external(manager)) if path else None
    path = found.path if found else None
    method = found.method if found else None
    targets, command = [], []
    reason = None
    if path is None:
        reason = 'No provider CLI is installed.'
    elif bundled(path):
        reason = 'This CLI is included with Aixand. Disable the provider to keep its bundled SDK and CLI files available for restoration.'
    else:
        if method in ('npm', 'brew'):
            command = package_removal(manager, path, method)
            if not command:
                reason = 'This CLI is managed externally, but its owning package manager could not be verified. Use its original package manager.'
        else:
            targets = native_targets(manager, path)
            if targets: method = 'installer'
    if path is not None and not targets and not command and not reason:
        reason = 'The installation owner could not be verified. Use manual uninstall for this installation.'
    native_commands = [('Remove-Item -LiteralPath ' + quote(target) +
                        (' -Recurse' if target.is_dir() and not target.is_symlink() else '') + ' -Force')
                       if os.name == 'nt' else 'rm ' + ('-rf ' if target.is_dir() and not target.is_symlink() else '-f ') + quote(target)
                       for target in targets] if not command else []
    manual = native_commands
    if not manual and not command and path is not None and not bundled(path):
        executable = manager.config['executable']
        manual = [('Get-Command -All ' + quote(executable)) if os.name == 'nt' else 'command -v ' + quote(executable)]
    from agent_manager.providers.installation.exports import receipt as command_receipt
    exported = command_receipt(manager.provider)
    from agent_manager.providers.installation.exports import owned_export_paths
    command_targets = []
    try:
        command_targets = owned_export_paths(manager, path)
    except Error as error:
        reason = str(error)
    identity = dict(provider=manager.provider, disabled=disabled(manager), method=method,
                    version=found.version if found else None,
                    path=fingerprint(path) if path else None,
                    targets=[fingerprint(target) for target in targets], command=command, terminalCommand=exported,
                    commandTargets=[fingerprint(target) for target in command_targets])
    revision = 'sha256:' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return dict(disabled=identity['disabled'], bundled=bundled(path), revision=revision,
                automatic=bool(targets or command) and not reason, reason=reason, method=method,
                version=identity['version'],
                path=str(path) if path else None, targets=[str(target) for target in targets], commandTargets=list(map(str,command_targets)),
                command=command, manualCommands=manual,
                manualSteps=['Open PowerShell on this machine.' if os.name == 'nt' else 'Open a terminal on this machine.',
                             'Stop this provider’s running agents before removing its CLI.',
                             'Run the removal command for this installation.' if targets else
                             'Identify the installation shown above and follow its official uninstall instructions.',
                             'Return to Aixand and choose Check installation.'],
                docs=DOCS.get(manager.provider), preservesAccounts=True)


def validate(manager, kind, revision, confirmed):
    current = plan(manager)
    if confirmed is not True or revision != current['revision']:
        raise Error('The provider installation changed. Refresh its details and confirm the action again.')
    if kind == 'uninstall' and not current['automatic']:
        raise Error(current['reason'] or 'Use manual uninstall for this installation.')
    return current


def perform(manager, kind, *, revision, confirmed, progress=lambda value: None):
    from agent_manager.providers.catalog import active_profiles
    from agent_manager.providers.installation import engine as install
    installation_root = install.state_root(manager.root.parent)
    with locks(manager.lock_root, 'provider-software'), \
            locks(installation_root / 'locks', 'install-' + manager.provider):
        current = validate(manager, kind, revision, confirmed)
        if active_profiles(manager):
            raise Error('Stop this provider’s running agents before changing its installation.')
        if kind in {'disable', 'enable'}:
            atomic(state_path(manager), json.dumps({'disabled': kind == 'disable'}).encode())
            return dict(disabled=kind == 'disable', accountsPreserved=True, bundledFilesPreserved=True)
        if kind != 'uninstall':
            raise Error('Unsupported provider software operation')
        from agent_manager.providers.installation.exports import validate_exports, remove as remove_commands
        validate_exports(manager,current['path'])
        progress({'progress': dict(schema='agent-manager/install-progress-1', stage='removing',
                                  label='Removing provider CLI', steps=dict(completed=1, total=3))})
        moved = []
        if current['command']:
            result = subprocess.run(current['command'], stdin=subprocess.DEVNULL)
            if result.returncode:
                raise Error('Package-manager uninstall failed. Check its output and retry.')
        try:
            for value in current['targets']:
                source = Path(value)
                destination = source.with_name(source.name + '.agent-uninstall-' + uuid.uuid4().hex)
                source.rename(destination)
                moved.append((source, destination))
        except OSError:
            for source, destination in reversed(moved):
                destination.rename(source)
            raise Error('The provider CLI is in use or its directory is not writable. Close its terminals and retry, or use manual uninstall.') from None
        try:
            for _, destination in moved:
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
        except OSError:
            raise Error('The CLI was detached, but some installation files could not be removed. Close its terminals and remove the .agent-uninstall directory beside the original CLI manually.') from None
        if current['path'] and os.path.lexists(current['path']):
            raise Error('The CLI is still installed. Check the operation terminal before retrying.')
        remove_commands(manager,current['path'])
        (installation_root / 'installations' / (manager.provider + '.json')).unlink(missing_ok=True)
        disabled_after = True
        atomic(state_path(manager), json.dumps({'disabled': disabled_after}).encode())
        progress({'progress': dict(schema='agent-manager/install-progress-1', stage='complete',
                                  label='Provider CLI removed', steps=dict(completed=3, total=3))})
        return dict(uninstalled=True, disabled=disabled_after, accountsPreserved=True)
