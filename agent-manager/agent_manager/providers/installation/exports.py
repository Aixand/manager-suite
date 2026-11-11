"""User-owned provider commands, independent of application homes and logins.

The application opts in with AIXAND_PROVIDER_STORE. Public launchers invoke the
official CLI directly; neither the desktop nor Agent Manager is needed to run
them. Only software and command receipts belong here, never credentials.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

from agent_manager.errors import Error
from agent_manager.storage.files import atomic
from agent_manager.storage.locks import locks

SCHEMA = 'aixand.provider-commands/1'
COMMANDS = {'codex': 'codex', 'claude': 'claude', 'opencode': 'opencode', 'copilot': 'copilot', 'antigravity': 'agy'}


def store():
    value = os.environ.get('AIXAND_PROVIDER_STORE')
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute() or str(path) in (str(path.anchor), str(Path.home())) or any(c in str(path) for c in '\0\r\n'):
        raise Error('The shared provider directory must be an absolute software directory.')
    for key in ('AIXAND_LOCAL_HOME', 'AIXAND_PROVIDER_BUNDLE_ROOT'):
        if os.environ.get(key):
            boundary = Path(os.environ[key]).absolute()
            if path.is_relative_to(boundary) or boundary.is_relative_to(path):
                raise Error('Shared provider software must be outside the application and workspace.')
    direct(path)
    return path


def direct(path):
    for entry in (path, *path.parents):
        if entry.is_symlink() or entry.exists() and getattr(entry.lstat(), 'st_file_attributes', 0) & 0x400:
            raise Error('The shared provider software path is redirected: ' + str(entry))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(root):
    path = root / 'installation-receipt.json'
    direct(path)
    if not path.exists():
        return dict(schema=SCHEMA, scope='shared', owner='user', providers={})
    value = json.loads(path.read_bytes())
    if value.get('schema') != SCHEMA or value.get('scope') != 'shared' or value.get('owner') != 'user' or not isinstance(value.get('providers'), dict) or not set(value['providers']) <= COMMANDS.keys():
        raise Error('The shared provider installation owner could not be verified.')
    for name, row in value['providers'].items():
        if row.get('provider') != name or not isinstance(row.get('exports'), dict):
            raise Error('Invalid provider command receipt.')
        allowed = {COMMANDS[name], 'aixand-' + COMMANDS[name]}
        allowed = {file for command in allowed for file in ([command + '.exe', command + '.aixand-command.json'] if os.name == 'nt' else [command])}
        if set(row['exports']) != allowed or not all(isinstance(v, str) and len(v) == 64 for v in row['exports'].values()):
            raise Error('Invalid provider command ownership records.')
    return value


def save(root, value):
    atomic(root / 'installation-receipt.json', (json.dumps(value, indent=2) + '\n').encode())


def npm_prefix(root):
    return root / 'packages/npm'


def node_path(root):
    return root / ('node/node.exe' if os.name == 'nt' else 'node/bin/node')








def receipt(provider):
    root = store()
    return read(root)['providers'].get(provider) if root else None


def canonical_path(spec, path):
    """Public launchers and retained migration copies are not extra installs."""
    root = store()
    provider = next((key for key, name in COMMANDS.items() if name == spec['executable']), None)
    row = read(root)['providers'].get(provider) if root else None
    if row:
        path = Path(path).absolute()
        if path.parent == root/'bin' and path.name in row['exports'] and path.is_file() and digest(path) == row['exports'][path.name]:
            return Path(row['path'])
        if row.get('migratedFrom') and path == Path(row['migratedFrom']):
            return Path(row['path'])
    return path


def managed_path(manager):
    root = store()
    if root is None or manager.provider not in COMMANDS or manager.config['executable'] != COMMANDS[manager.provider]:
        return None
    row = read(root)['providers'].get(manager.provider)
    if row and Path(row['path']).is_file():
        return Path(row['path'])
    from agent_manager.providers.installation.engine import catalog
    spec = catalog().get(manager.provider, {})
    path = npm_prefix(root) / (spec.get('executable', '') + '.cmd' if os.name == 'nt' else 'bin/' + spec.get('executable', ''))
    return path if spec.get('npm_package') and path.is_file() else None


def node_for(path):
    root = store()
    return node_path(root) if root and Path(path).absolute().is_relative_to(npm_prefix(root)) else None


def policy():
    root = store()
    if root and (root/'installation-receipt.json').is_file():
        saved = read(root).get('policy')
        if saved in ('keep', 'prefer'):
            return saved
    value = os.environ.get('AIXAND_PROVIDER_COMMAND_POLICY')
    path = os.environ.get('AIXAND_PROVIDER_COMMAND_POLICY_FILE')
    if path and Path(path).is_file():
        value = (json.loads(Path(path).read_bytes()).get('terminal') or {}).get('policy', value)
    if value not in (None, 'keep', 'prefer'):
        raise Error('Unsupported terminal command preference.')
    return value or 'keep'


def search_path():
    if os.name != 'nt':
        return os.environ.get('AIXAND_PROVIDER_USER_PATH', os.environ.get('PATH', ''))
    import winreg
    def read_path(hive, key):
        try:
            with winreg.OpenKey(hive, key) as handle:
                return winreg.QueryValueEx(handle, 'Path')[0]
        except FileNotFoundError:
            return ''
    return os.path.expandvars(os.pathsep.join([read_path(winreg.HKEY_LOCAL_MACHINE,
        r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'), read_path(winreg.HKEY_CURRENT_USER, 'Environment')]))


def ordered_path(entries, directory, preference):
    key = lambda value: os.path.normcase(os.path.abspath(os.path.expandvars(value.strip('"'))))
    same = lambda value: key(value) == key(directory)
    entries = [value for value in entries if value]
    if preference == 'keep' and any(same(value) for value in entries):
        return entries
    retained = [value for value in entries if not same(value)]
    return [directory, *retained] if preference == 'prefer' else [*retained, directory]


def register_path(root, preference):
    directory = str(root / 'bin')
    if os.environ.get('AIXAND_SKIP_PATH_REGISTRATION') == '1':
        return dict(registered=False, directory=directory, policy=preference, restartTerminal=True)
    if os.name == 'nt':
        import winreg
        from ctypes import wintypes
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, 'Environment') as handle:
            try:
                previous, kind = winreg.QueryValueEx(handle, 'Path')
            except FileNotFoundError:
                previous, kind = '', winreg.REG_EXPAND_SZ
            if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                raise Error('Unexpected user PATH registry value.')
            current = os.pathsep.join(ordered_path(previous.split(os.pathsep), directory, preference))
            if current != previous:
                winreg.SetValueEx(handle, 'Path', 0, kind, current)
        api = ctypes.WinDLL('user32', use_last_error=True)
        api.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        result = ctypes.c_size_t()
        api.SendMessageTimeoutW(0xffff, 0x1a, 0, 'Environment', 2, 3000, ctypes.byref(result))
    else:
        shell = Path(os.environ.get('AIXAND_PROVIDER_USER_SHELL', os.environ.get('SHELL', '/bin/bash'))).name
        if shell == 'fish':
            paths = [Path(os.environ.get('AIXAND_PROVIDER_USER_CONFIG_HOME', os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))) / 'fish/conf.d/aixand-providers.fish']
            block = 'fish_add_path ' + ('--prepend' if preference == 'prefer' else '--append') + ' --path ' + shlex.quote(directory)
        else:
            paths = [Path.home()/name for name in (('.zprofile', '.zshrc') if shell == 'zsh' else ('.profile', '.bashrc'))]
            value = shlex.quote(directory)
            block = 'export PATH=' + (value + ':"$PATH"' if preference == 'prefer' else '"$PATH":' + value)
        begin, end = '# >>> Aixand provider commands >>>', '# <<< Aixand provider commands <<<'
        for path in paths:
            direct(path)
            previous = path.read_text() if path.exists() else ''
            if begin in previous or end in previous:
                if previous.count(begin) != 1 or previous.count(end) != 1 or previous.index(begin) > previous.index(end):
                    raise Error('The provider PATH configuration block needs manual review: ' + str(path))
                start, finish = previous.index(begin), previous.index(end) + len(end)
                current = previous[:start] + begin + '\n' + block + '\n' + end + previous[finish:]
            else:
                current = previous.rstrip('\n') + '\n\n' + begin + '\n' + block + '\n' + end + '\n'
            if current != previous:
                mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
                atomic(path, current.encode())
                path.chmod(mode)
    return dict(registered=True, directory=directory, policy=preference, restartTerminal=True,
                **({'profileFiles': list(map(str, paths)), 'profileBlock': begin+'\n'+block+'\n'+end} if os.name!='nt' else {}))


def status(manager):
    root = store()
    if root is None or manager.provider not in COMMANDS:
        return None
    name = COMMANDS[manager.provider]
    result = dict(command=name, alias='aixand-'+name, ready=False, registered=False,
        directory=str(root/'bin'), path=None, defaultPath=None, usingExisting=False,
        restartTerminal=True, policy=policy(), error=None, hasExisting=False, existingPaths=[])
    try:
        row = read(root)['providers'].get(manager.provider)
        existing = []
        for directory in search_path().split(os.pathsep):
            candidate = shutil.which(name, path=directory) if directory else None
            if candidate and Path(candidate).absolute().parent != root/'bin' and (not row or
                Path(candidate).resolve()!=Path(row['path']).resolve() and candidate!=row.get('migratedFrom')):
                if candidate not in existing:
                    existing.append(candidate)
        result.update(hasExisting=bool(existing), existingPaths=existing)
        if not row:
            result['error'] = 'Terminal command setup is required.'
            return result
        result.update(path=str(root/'bin'/(name+'.exe' if os.name=='nt' else name)), version=row['version'])
        for filename, expected in row['exports'].items():
            path = root/'bin'/filename
            direct(path)
            if not path.is_file() or digest(path) != expected:
                raise Error('The terminal launcher needs repair: '+str(path))
        if not Path(row['path']).is_file():
            raise Error('The provider executable is missing. Check its installation.')
        effective = search_path()
        registration = read(root).get('registration', row.get('registration', {}))
        if os.name != 'nt' and registration.get('registered'):
            files = registration.get('profileFiles', [])
            block = registration.get('profileBlock')
            if files and block and all(Path(path).is_file() and block in Path(path).read_text() for path in files):
                effective = os.pathsep.join(ordered_path(effective.split(os.pathsep), str(root/'bin'), registration['policy']))
        found = shutil.which(name, path=effective)
        alias = shutil.which('aixand-'+name, path=effective)
        result.update(ready=bool(alias and Path(alias).absolute()==root/'bin'/Path(alias).name),
            registered=bool(alias and Path(alias).absolute()==root/'bin'/Path(alias).name), defaultPath=found,
            usingExisting=bool(found and Path(found).absolute()!=Path(result['path'])))
        if not result['ready']:
            result['error'] = 'The provider command directory is not available in new terminals. Repair terminal command setup.'
    except (OSError, ValueError, Error) as error:
        result['error'] = str(error)
    return result


def _launch_files(root, name, program, args, version, label):
    commands = (name, 'aixand-'+name)
    if os.name == 'nt':
        source = Path(os.environ.get('AIXAND_PROVIDER_LAUNCHER_SOURCE', ''))
        if not source.is_absolute() or not source.is_file():
            raise Error('The native provider command launcher needs repair.')
        binary = source.read_bytes()
        bundle = os.environ.get('AIXAND_PROVIDER_BUNDLE_ROOT')
        metadata = Path(bundle)/'products/consumer/windows_metadata.py' if bundle else None
        if metadata and metadata.is_file():
            # Stamp a disposable copy before hashing/publishing it. The official
            # CLI is untouched; the launcher reports the CLI's product version.
            with tempfile.TemporaryDirectory(prefix='provider-command-') as temporary:
                target = Path(temporary)/(name+'.exe')
                target.write_bytes(binary)
                code = 'import sys;sys.path.insert(0,sys.argv[1]);from windows_metadata import stamp;stamp(sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5],launcher=True)'
                subprocess.run([sys.executable,'-I','-B','-c',code,str(metadata.parent),str(target),version,label,name],check=True,capture_output=True,timeout=30)
                binary = target.read_bytes()
        return {filename: data for command in commands for filename, data in (
            (command+'.exe', binary),
            (command+'.aixand-command.json', (json.dumps(dict(schema='aixand.command/1', root=str(root),
                commands={command: dict(program=str(program), args=list(args))}), indent=2)+'\n').encode()))}
    argv = [str(program), *[str(root/value[6:]) if value.startswith('@root/') else value for value in args]]
    content = ('#!/bin/sh\nPATH='+shlex.quote(str(root/'bin'))+':"$PATH"\nexport PATH\nexec '+shlex.join(argv)+' "$@"\n').encode()
    return {command: content for command in commands}


def register(manager, *, preference=None, progress=lambda value: None):
    """Export a native CLI without installing software or migrating accounts."""
    from agent_manager.providers.installation import engine as install
    root = store()
    if root is None or manager.provider not in COMMANDS:
        return None
    root.mkdir(parents=True, exist_ok=True)
    if preference not in (None, 'keep', 'prefer'):
        raise Error('Unsupported terminal command preference.')
    with locks(root/'locks', 'commands'):
        state = read(root)
        spec = install.catalog()[manager.provider]
        path = install.provider_executable(manager)
        if path is None:
            raise Error('Install the provider CLI before setting up its terminal command.')
        found = install.inspect(spec, path)
        if found.error:
            raise Error(found.error)
        if found.method in ('npm', 'brew'):
            raise Error('Terminal command setup requires a native provider installation. Migrate external package-manager software manually.')
        program, args = path, []
        files = _launch_files(root, spec['executable'], program, args, found.version, spec['label'])
        previous = state['providers'].get(manager.provider, {}).get('exports', {})
        directory = root/'bin'
        direct(directory)
        directory.mkdir(exist_ok=True)
        originals = {}
        for filename in files:
            target = directory/filename
            direct(target)
            originals[filename] = target.read_bytes() if target.exists() else None
            if target.exists() and (filename not in previous or digest(target) != previous[filename]):
                raise Error('A terminal command was replaced outside Aixand. Keep it and resolve this path before retrying: '+str(target))
        changed = []
        try:
            for filename, content in files.items():
                atomic(directory/filename, content)
                (directory/filename).chmod(0o755 if not filename.endswith('.json') else 0o600)
                changed.append(filename)
            # Exercise the exported executable with no application PATH or
            # profile overrides. A valid receipt alone is not terminal readiness.
            environment = {key:value for key,value in os.environ.items() if key not in
                {'PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','CODEX_HOME','CLAUDE_CONFIG_DIR','COPILOT_HOME'} and not key.startswith('AIXAND_')}
            environment['PATH'] = os.pathsep.join([str(directory), str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32')]) if os.name=='nt' else '/usr/bin:/bin'
            launcher = directory/(spec['executable']+'.exe' if os.name=='nt' else spec['executable'])
            result = subprocess.run([str(launcher),*install.version_arguments(spec)],env=environment,stdin=subprocess.DEVNULL,capture_output=True,timeout=20,
                text=True,encoding='utf-8',errors='replace',creationflags=0x08000000 if os.name=='nt' else 0)
            if result.returncode or install.version_from_output(result.stdout+'\n'+result.stderr) != found.version:
                raise Error('The exported provider command failed verification outside Aixand.')
            preference = preference or policy()
            registration = register_path(root, preference)
            state.update(policy=preference,registration=registration)
            state['providers'][manager.provider] = dict(provider=manager.provider,path=str(path),version=found.version,method=found.method,
                exports={file:hashlib.sha256(data).hexdigest() for file,data in files.items()},registration=registration)
            save(root,state)
        except BaseException:
            for filename in reversed(changed):
                target=directory/filename
                if originals[filename] is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic(target,originals[filename])
            raise
        return status(manager)


def validate_exports(manager, path=None):
    root=store()
    row=receipt(manager.provider)
    if not root or not row:
        return
    if path is not None and Path(path).absolute()!=Path(row['path']).absolute():
        return
    for filename,expected in row['exports'].items():
        path=root/'bin'/filename
        direct(path)
        if path.exists() and digest(path)!=expected:
            raise Error('A provider command was replaced outside Aixand; it was retained: '+str(path))


def owned_export_paths(manager, path=None):
    validate_exports(manager,path)
    root=store()
    row=receipt(manager.provider)
    if row and path is not None and Path(path).absolute()!=Path(row['path']).absolute():
        return []
    return [root/'bin'/file for file in row['exports']] if root and row else []


def runtime_status():
    root=store()
    if not root or not (root/'node/runtime-receipt.json').is_file():
        return None
    direct(root/'node')
    record=json.loads((root/'node/runtime-receipt.json').read_bytes())
    if record.get('schema')!=1 or not node_path(root).is_file() or digest(node_path(root))!=record.get('sha256'):
        raise Error('The shared provider runtime owner could not be verified.')
    return dict(path=str(root/'node'),providers=[name for name,row in read(root)['providers'].items() if row.get('npm')])


def remove_runtime():
    root=store()
    if root is None:
        return
    with locks(root/'locks','commands'), locks(root/'locks','software',wait=True):
        current=runtime_status()
        if current is None:
            return
        packages=npm_prefix(root)/('node_modules' if os.name=='nt' else 'lib/node_modules')
        if current['providers'] or packages.exists() and next(packages.rglob('package.json'),None) is not None:
            raise Error('Remove the provider CLIs using this shared runtime before deleting it.')
        shutil.rmtree(root/'node')


def remove(manager, path=None):
    root=store()
    if root is None:
        return
    with locks(root/'locks','commands'):
        state=read(root)
        row=state['providers'].get(manager.provider)
        if not row:
            return
        if path is not None and Path(path).absolute()!=Path(row['path']).absolute():
            return
        validate_exports(manager,path)
        for filename in row['exports']:
            (root/'bin'/filename).unlink(missing_ok=True)
        del state['providers'][manager.provider]
        save(root,state)
        # Runtime removal has its own explicit cleanup choice. Other terminals
        # and concurrent provider installs can still need this Node copy.
