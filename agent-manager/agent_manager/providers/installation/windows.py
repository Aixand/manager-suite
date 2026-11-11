"""Native Windows vendor installers and OpenCode's official standalone CLI."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

from agent_manager.errors import Error
from agent_manager.storage.files import atomic
from agent_manager.storage.locks import locks
from agent_manager.providers.installation.progress import InstallationProgress

URLS = {'codex': 'https://chatgpt.com/codex/install.ps1',
        'claude': 'https://claude.ai/install.ps1',
        'agy': 'https://antigravity.google/cli/install.ps1'}


def powershell():
    return Path(os.environ.get('SystemRoot', os.environ.get('SYSTEMROOT', 'C:/Windows'))) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


def destination(spec):
    home = Path.home()
    if spec['executable'] == 'codex':
        return Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local')) / 'Programs/OpenAI/Codex/bin/codex.exe'
    if spec['executable'] == 'opencode':
        return home / '.opencode/bin/opencode.exe'
    if spec['executable'] == 'agy':
        return Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local')) / 'agy/bin/agy.exe'
    return home / '.local/bin' / (spec['executable'] + '.exe')


def prerequisites(spec, method, version=None):
    if spec.get('pinned_artifact') and method == 'installer':
        from agent_manager.providers.policy import artifact
        try:
            artifact(spec['policy_provider'], 'Windows', platform.machine(), version)
            return None
        except (KeyError, ValueError) as error:
            return str(error)
    if platform.machine().lower() not in ('amd64', 'x86_64', 'arm64', 'aarch64'):
        return 'Requires an x64 or ARM64 machine'
    if method != 'installer':
        return ('Windows requires the official standalone OpenCode CLI.' if spec['executable'] == 'opencode'
                else 'Use the native Windows installer.')
    if spec['executable'] not in ('codex', 'opencode', 'claude', 'agy'):
        return 'Use this provider’s official Windows installation instructions.'
    if spec['executable'] in URLS and not powershell().is_file():
        return 'Windows PowerShell is unavailable.'
    return None


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith('https://'):
            raise Error('The provider download redirected outside HTTPS.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, target, reporter, *, maximum=512 * 1024 * 1024):
    if not url.startswith('https://'):
        raise Error('Provider downloads require HTTPS.')
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 Aixand-Agent-Manager'})
    opener = urllib.request.build_opener(HTTPSRedirect())
    with opener.open(request, timeout=120) as response, target.open('wb') as output:
        if response.status != 200:
            raise Error('The official provider download failed.')
        size = response.headers.get('Content-Length', '')
        total = int(size) if size.isdigit() else None
        completed = 0
        while chunk := response.read(256 * 1024):
            completed += len(chunk)
            if completed > maximum:
                raise Error('The provider download exceeded its size limit.')
            output.write(chunk)
            reporter.report('downloading', transfer=dict(completed=completed,
                            total=total if total is None or total >= completed else None, unit='bytes'))
    if not completed:
        raise Error('The official provider download was empty.')


def opencode_download(directory, version, reporter):
    from agent_manager.providers.terminal_policy import recommended
    version = version or recommended('opencode')
    arch = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x64'
    return f'https://opencode.ai/files/bin/{version}/opencode-windows-{arch}.zip'


def add_user_path(directory):
    if os.environ.get('AGENT_MANAGER_SKIP_PATH_REGISTRATION') == '1':
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
        try:
            previous, kind = winreg.QueryValueEx(key, 'Path')
        except FileNotFoundError:
            previous, kind = '', winreg.REG_EXPAND_SZ
        entries = [part for part in previous.split(';') if part]
        if not any(os.path.expandvars(part).rstrip('\\').casefold() == str(directory).rstrip('\\').casefold() for part in entries):
            winreg.SetValueEx(key, 'Path', 0, kind, ';'.join([*entries, str(directory)]))
    import ctypes
    result = ctypes.c_size_t()
    ctypes.windll.user32.SendMessageTimeoutW(0xffff, 0x001A, 0, 'Environment',
                                           0x0002, 2000, ctypes.byref(result))


def perform(target, spec, method, version, reinstall, yes, *, update_check=None, progress=None, installation_plan=None, root=None):
    from agent_manager.providers.installation import engine as install
    from agent_manager.providers.installation.releases import for_install
    version = for_install(target, version, method=method, plan=installation_plan)
    if spec.get('pinned_artifact'):
        from agent_manager.providers.installation.artifacts import perform as install_artifact
        return install_artifact(target, spec, version, reinstall, update_check=update_check,
                                progress=progress, installation_plan=installation_plan, root=root)
    reporter = InstallationProgress(progress, method)
    reporter.report('preparing')
    problem = prerequisites(spec, method, version)
    if problem:
        raise Error(problem)
    expected = destination(spec)
    root = install.state_root(root)
    with locks(root / 'locks', 'install-' + target):
        found = install.discover(spec)
        if update_check:
            update_check(found)
        problem = install.migration_error(found, method)
        if problem:
            raise Error(problem)
        if found and not reinstall and not found[0].error and (not version or found[0].version == version):
            reporter.report('complete', existing=True)
            return 0
        if os.path.lexists(expected) and not any(item.path.absolute() == expected.absolute() for item in found):
            raise Error('The installation destination is occupied. Resolve it manually before installing.')
        with install.native_replacement(expected, bool(found)), tempfile.TemporaryDirectory(prefix='agent-windows-install-') as temporary:
            directory = Path(temporary)
            if target == 'opencode':
                archive = directory / 'provider.zip'
                download(opencode_download(directory, version, reporter), archive, reporter)
                with zipfile.ZipFile(archive) as bundle:
                    matches = [entry for entry in bundle.infolist() if Path(entry.filename).name == 'opencode.exe'
                               and not entry.is_dir() and entry.file_size <= 512 * 1024 * 1024]
                    if len(matches) != 1:
                        raise Error('The official OpenCode archive did not contain one CLI executable.')
                    binary = directory / 'opencode.exe'
                    with bundle.open(matches[0]) as source, binary.open('wb') as output:
                        shutil.copyfileobj(source, output)
                checked = install.inspect(spec, binary)
                if checked.error or version and checked.version != version:
                    raise Error('The downloaded OpenCode CLI failed its version check.')
                reporter.report('installing')
                expected.parent.mkdir(parents=True, exist_ok=True)
                staging = expected.with_name(expected.name + '.installing')
                shutil.copyfile(binary, staging)
                staging.replace(expected)
                add_user_path(expected.parent)
            else:
                script = directory / 'install.ps1'
                download(URLS[spec['executable']], script, reporter, maximum=4 * 1024 * 1024)
                if b'<html' in script.read_bytes()[:1024].lower():
                    raise Error('The official installer returned an HTML page. Nothing was executed.')
                reporter.report('installing')
                env = os.environ.copy()
                if target == 'codex':
                    env.update(CODEX_NON_INTERACTIVE='1', CODEX_INSTALL_DIR=str(expected.parent),
                               CODEX_RELEASE=version, CODEX_INSTALL_DEFER_SELECTION='0')
                args = [str(powershell()), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)]
                if target == 'claude' and version:
                    args.append(version)
                if target == 'antigravity':
                    args += ['--dir', str(expected.parent)]
                install.run_checked(args, cwd=directory, env=env)
            reporter.report('verifying')
            if not expected.is_file():
                raise Error('The installer completed without creating the expected provider CLI.')
            checked = install.inspect(spec, expected)
            if checked.error or version and checked.version != version:
                raise Error('The installed provider CLI failed its version check.')
        if target == 'antigravity':
            add_user_path(expected.parent)
        receipt = dict(target=target, method='installer', path=str(expected), version=checked.version,
                       softwareOnly=True, sha256=hashlib.sha256(expected.read_bytes()).hexdigest())
        from agent_manager.providers.installation.plans import receipt_metadata
        receipt.update(receipt_metadata(version, installation_plan))
        atomic(root / 'installations' / (target + '.json'), json.dumps(receipt).encode())
        print(f'Installed {spec["label"]} {checked.version}: {expected}', flush=True)
        reporter.report('complete')
        return 0
