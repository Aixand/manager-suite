"""Native CLI release compatibility and verified installation artifacts."""
from functools import lru_cache
import hashlib
from pathlib import Path
import re
import subprocess
import tomllib

_VERSION = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?')


@lru_cache(maxsize=1)
def manifest():
    data = tomllib.loads(Path(__file__).with_name('provider-policy.toml').read_text())
    if data.get('schema') != 1:
        raise ValueError('Unsupported provider policy schema')
    return data


def revision():
    digest = hashlib.sha256(Path(__file__).with_name('provider-policy.toml').read_bytes())
    return 'sha256:' + digest.hexdigest()


def version_tuple(value):
    match = _VERSION.fullmatch(str(value or '').strip().removeprefix('v'))
    if match is None or match.group(4):
        return None
    return tuple(map(int, match.groups()[:3]))


def cli_version(value):
    value = re.sub(r'(?<=\w)\.(?=\s|$)', '', str(value or ''))
    match = re.search(r'(?<![\w.])v?(' + _VERSION.pattern + r')(?![\w.])', value)
    return match.group(1).rstrip('.') if match else None


def inspect_cli(provider, argv, *, env=None):
    version = None
    try:
        result = subprocess.run([*argv, '--version'], env=env, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=10, check=False)
        if result.returncode == 0:
            version = cli_version(result.stdout.decode('utf-8', 'replace'))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return compatibility(provider, version)


def in_range(version, specifier):
    value = version_tuple(version)
    if value is None:
        return False
    for clause in specifier.split(','):
        match = re.fullmatch(r'\s*(>=|<=|==|!=|>|<)\s*([0-9]+\.[0-9]+\.[0-9]+)\s*', clause)
        if match is None:
            raise ValueError('Invalid provider compatibility range: ' + specifier)
        op, target = match.groups()
        boundary = version_tuple(target)
        if boundary is None:
            raise ValueError('Invalid provider compatibility version: ' + target)
        if not {'>=': value >= boundary, '<=': value <= boundary, '==': value == boundary,
                '!=': value != boundary, '>': value > boundary, '<': value < boundary}[op]:
            return False
    return True


def component(provider):
    return manifest()['providers'][provider]['cli']


def recommended(provider):
    return component(provider)['install_version']


def compatibility(provider, version):
    policy = component(provider)
    specifier = policy['supported_range']
    normalized = str(version or '').strip().removeprefix('v')
    valid = version_tuple(normalized) is not None
    blocked = valid and any(version_tuple(normalized) == version_tuple(value) for value in policy.get('blocked_versions', []))
    compatible = valid and in_range(normalized, specifier) and not blocked
    status = 'compatible' if compatible else 'missing' if not version else 'incompatible' if valid else 'unknown'
    label = policy.get('label', policy.get('package', provider))
    reason = None if compatible else (
        f'{label} {version} is outside the supported range {specifier}. Aixand recommends {policy["install_version"]}.'
        if valid else f'Could not determine a supported {label} version. Aixand recommends {policy["install_version"]}.')
    if blocked:
        reason = f'{label} {version} has a known incompatibility. Aixand recommends {policy["install_version"]}.'
    return dict(status=status, compatible=compatible, tested=compatible and normalized in policy['tested_versions'],
                installedVersion=version, recommendedVersion=policy['install_version'], supportedRange=specifier,
                testedVersions=list(policy['tested_versions']), policyRevision=revision(), reason=reason)


def artifact(provider, system, architecture, version=None):
    os_name = {'Windows': 'windows', 'Darwin': 'darwin', 'Linux': 'linux'}[system]
    arch = {'x86_64': 'amd64', 'amd64': 'amd64', 'arm64': 'arm64', 'aarch64': 'arm64'}[architecture.lower()]
    record = manifest().get('artifacts', {}).get(provider, {}).get(os_name + '_' + arch)
    selected = version or recommended(provider)
    if not record or record['version'] != selected:
        raise ValueError(f'Aixand has no verified {provider} {selected} download for {os_name}/{arch}.')
    if not record['url'].startswith('https://') or not re.fullmatch('[a-f0-9]{128}', record['sha512']):
        raise ValueError('Invalid provider artifact identity')
    return dict(record)
