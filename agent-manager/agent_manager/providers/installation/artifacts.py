"""Install a pinned vendor artifact after checksum and executable verification."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request

from agent_manager.errors import Error
from agent_manager.storage.files import atomic
from agent_manager.storage.locks import locks
from agent_manager.providers.installation.progress import InstallationProgress


def perform(provider, spec, version, reinstall, *, update_check=None, progress=None, installation_plan=None, root=None):
    from agent_manager.providers.installation import engine as install
    from agent_manager.providers.policy import artifact
    from agent_manager.providers.installation.releases import for_install
    version = for_install(provider, version, method='installer', plan=installation_plan)
    try:
        source = artifact(provider, install.platform.system(), install.platform.machine(), version)
    except (KeyError, ValueError) as error:
        raise Error(str(error)) from error
    reporter = InstallationProgress(progress, 'installer')
    reporter.report('preparing')
    expected = install.native_binary(spec)
    root = install.state_root(root)
    with locks(root / 'locks', 'install-' + provider):
        found = install.discover(spec)
        if update_check:
            update_check(found)
        problem = install.migration_error(found, 'installer')
        if problem:
            raise Error(problem)
        if found and not reinstall:
            if not found[0].error and found[0].version == version:
                reporter.report('complete', existing=True)
                return 0
            raise Error('Use explicit reinstall to change the installed provider version.')
        if os.path.lexists(expected) and not any(row.path.absolute() == expected.absolute() for row in found):
            raise Error('The provider installation destination is occupied.')
        with tempfile.TemporaryDirectory(prefix='agent-provider-artifact-') as temporary:
            directory = Path(temporary)
            payload = directory / 'payload'
            digest, completed = hashlib.sha512(), 0
            from agent_manager.providers.installation.windows import HTTPSRedirect
            opener = urllib.request.build_opener(HTTPSRedirect())
            with opener.open(source['url'], timeout=120) as response, payload.open('wb') as stream:
                length = response.headers.get('Content-Length', '')
                total = int(length) if length.isdigit() else None
                while chunk := response.read(256 * 1024):
                    completed += len(chunk)
                    if completed > 512 * 1024 * 1024:
                        raise Error('Provider artifact exceeds the download limit.')
                    digest.update(chunk)
                    stream.write(chunk)
                    reporter.report('downloading', transfer=dict(completed=completed,
                        total=total if total is None or total >= completed else None, unit='bytes'))
            if not completed or digest.hexdigest() != source['sha512']:
                raise Error('The provider artifact checksum failed verification.')
            binary = directory / (spec['executable'] + ('.exe' if install.platform.system() == 'Windows' else ''))
            if source['url'].endswith('.exe'):
                shutil.copyfile(payload, binary)
            else:
                with tarfile.open(payload) as archive:
                    member = source.get('member', 'antigravity' if provider == 'antigravity' else spec['executable'])
                    matches = [row for row in archive.getmembers() if row.isfile() and Path(row.name).name == member
                               and row.size <= 512 * 1024 * 1024]
                    if len(matches) != 1:
                        raise Error('The provider archive must contain one CLI executable.')
                    with archive.extractfile(matches[0]) as src, binary.open('wb') as dest:
                        shutil.copyfileobj(src, dest)
            binary.chmod(0o755)
            reporter.report('verifying')
            checked = install.inspect(spec, binary)
            if checked.error or checked.version != version:
                raise Error(f'The vendor artifact did not contain the requested {version} CLI.')
            reporter.report('installing')
            expected.parent.mkdir(parents=True, exist_ok=True)
            staging = expected.with_name(expected.name + '.installing')
            try:
                shutil.copy2(binary, staging)
                staging.replace(expected)
            finally:
                staging.unlink(missing_ok=True)
        if install.platform.system() == 'Windows':
            from agent_manager.providers.installation.windows import add_user_path
            add_user_path(expected.parent)
        receipt = dict(target=provider, method='installer', path=str(expected), version=version,
                       softwareOnly=True, sha256=hashlib.sha256(expected.read_bytes()).hexdigest(),
                       artifact=source)
        from agent_manager.providers.installation.plans import receipt_metadata
        receipt.update(receipt_metadata(version, installation_plan))
        atomic(root / 'installations' / (provider + '.json'), json.dumps(receipt).encode())
        reporter.report('complete')
        print(f'Installed {spec["label"]} {version}: {expected}', flush=True)
        return 0
