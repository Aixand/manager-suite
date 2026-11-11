"""Agent Manager terminal recommendations and bounded history-adapter support.

Reading policy never runs a CLI. Shared CLI/SDK compatibility remains authoritative
for ordinary use; continuation applies an additional, narrower version limit.
"""
from functools import lru_cache
import hashlib
from pathlib import Path
import tomllib

from agent_manager.providers import policy

PATH = Path(__file__).with_name('terminal-policy.toml')


@lru_cache(maxsize=1)
def manifest():
    data = tomllib.loads(PATH.read_text())
    if data.get('schema') != 1:
        raise ValueError('Unsupported terminal policy schema')
    for provider, row in data['providers'].items():
        version = row['install_version']
        versions = row['install_versions']
        if (not isinstance(versions, list) or not versions or any(not isinstance(v, str) or policy.version_tuple(v) is None for v in versions)
                or len(set(versions)) != len(versions) or version not in versions):
            raise ValueError('Invalid terminal installation catalog: ' + provider)
        feature = row.get('continuation')
        continuation_version = feature.get('recommended_version', version) if feature else None
        if (not policy.compatibility(provider, version)['compatible']
                or feature and (continuation_version not in versions
                    or not policy.compatibility(provider, continuation_version)['compatible']
                    or not policy.in_range(continuation_version, feature['supported_range']))):
            raise ValueError('Terminal recommendation does not support continuation: ' + provider)
    return data


def revision():
    return 'sha256:' + hashlib.sha256(PATH.read_bytes()).hexdigest()


def recommended(provider):
    row = manifest()['providers'].get(provider)
    return row['install_version'] if row else policy.recommended(provider)


def compatibility(provider, version):
    """Ordinary CLI compatibility with Agent Manager's installation recommendation."""
    result = policy.compatibility(provider, version)
    target = recommended(provider)
    if result['reason'] and result['recommendedVersion'] != target:
        result['reason'] = (f'{provider} {version or "(unknown version)"} does not satisfy the general CLI policy '
                            f'{result["supportedRange"]} or is blocked by that policy. Agent Manager recommends {target}.')
    return dict(result, recommendedVersion=target, terminalPolicyRevision=revision())


def requirements(provider, *, mechanism=None):
    row = manifest()['providers'].get(provider)
    if row is None or not row.get('continuation') or mechanism is not None and row['continuation']['mechanism'] != mechanism:
        return None
    feature = row['continuation']
    return dict(supportedRange=feature['supported_range'],
                recommendedVersion=feature.get('recommended_version', row['install_version']),
                platforms=list(feature['platforms']), prerequisites=list(feature['prerequisites']),
                verification=feature['verification'], verificationNotes=feature['verification_notes'],
                policyRevision=revision())


def continuation(provider, version=None, *, checked=False, mechanism=None):
    result = requirements(provider, mechanism=mechanism)
    if result is None:
        return None
    compatible = checked and policy.compatibility(provider, version)['compatible'] and policy.in_range(version, result['supportedRange'])
    # Exact native-adapter pins also exclude unverified vendor/build variants.
    # Codex's bounded range retains the general policy's build-version handling.
    if compatible and result['supportedRange'].startswith('=='):
        compatible = str(version).strip().removeprefix('v') == result['supportedRange'][2:]
    return dict(result, installedVersion=version, compatible=bool(compatible) if checked else None,
                status='unchecked' if not checked else 'compatible' if compatible else 'missing' if not version else 'incompatible',
                reason=None if not checked or compatible else version_error(provider))


def supports_continuation(provider, version):
    result = continuation(provider, version, checked=True)
    return bool(result and result['compatible'])


def version_error(provider):
    feature = requirements(provider)
    target = feature['recommendedVersion']
    required = feature['supportedRange'].removeprefix('==')
    return (f'{provider} cross-environment continuation requires native CLI {required}; '
            f'Agent Manager recommends {target}. To change the installed version explicitly, run '
            f'agent install {provider} --reinstall --version {target} --yes. '
            'Ordinary same-environment continuation uses the native CLI without this adapter.')
