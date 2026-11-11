"""Resolve local, approved CLI releases before invoking any installer.

The finite installation catalog and broader existing-CLI compatibility are distinct.
Selectors never resolve through npm dist-tags or a vendor's moving latest channel.
"""
import hashlib
import platform
from pathlib import Path
import re

from agent_manager.errors import Error
from agent_manager.providers import policy, terminal_policy

SELECTORS = ('recommended', 'latest')
EXACT = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z')


def recipe_revision():
    return 'sha256:' + hashlib.sha256(Path(__file__).with_name('install_catalog.toml').read_bytes()).hexdigest()


def selector(value):
    if value is None: return None
    if not isinstance(value, str): raise Error('Version must be latest, recommended, or an exact release')
    if value.startswith('v') and EXACT.fullmatch(value[1:]): value = value[1:]
    if value not in SELECTORS and not EXACT.fullmatch(value):
        raise Error('--version must be latest, recommended, or an exact release; URLs, package names and ranges are not accepted')
    return value


def exact(provider, value):
    """Low-level guard: even direct method execution enforces CLI compatibility."""
    value = selector(value)
    if value is None: return None
    if value in SELECTORS: raise Error('Resolve the version selector to an exact release before installation')
    result = terminal_policy.compatibility(provider, value)
    if not result['compatible']: raise Error(result['reason'])
    return value


def versions(provider):
    row = terminal_policy.manifest()['providers'].get(provider)
    if row is None: raise Error('This provider has no approved CLI release catalog')
    return sorted(row['install_versions'], key=policy.version_tuple, reverse=True)


def for_install(provider, version, *, method, plan=None):
    if method != 'installer':
        raise Error('Only official native installers are supported.')
    version = exact(provider, version or terminal_policy.recommended(provider))
    preserving = plan and plan.get('kind') == 'repair' and plan.get('versionTarget') is None and plan.get('existingVersion') == version
    if preserving: return version
    return resolve(provider, version, method=method)


def methods(provider, version, *, method=None):
    from agent_manager.providers.installation import engine
    spec = engine.catalog()[provider]
    choices = [method] if method is not None else engine.supported_methods(spec)
    return [dict(id=choice, error=engine.method_support(spec, choice, version)) for choice in choices]


def resolve(provider, value=None, *, method=None):
    if method is not None and method != 'installer':
        raise Error('Only official native installers are supported.')
    selected = selector(value) or 'recommended'
    choices = versions(provider) if selected == 'latest' else [terminal_policy.recommended(provider) if selected == 'recommended' else selected]
    for version in choices:
        result = terminal_policy.compatibility(provider, version)
        if not result['compatible']:
            if selected != 'latest': raise Error(result['reason'])
            continue
        if version not in versions(provider):
            raise Error(f'{provider} {version} is compatible but is not approved for managed installation. Use agent versions {provider}.')
        available = methods(provider, version, method=method)
        if any(row['error'] is None for row in available): return version
        if selected != 'latest':
            raise Error(next((row['error'] for row in available if row['error']), 'No versioned installation method is available'))
    raise Error(f'No approved {provider} release is installable with this method on this host')


def catalog(provider, *, method=None):
    try: latest = resolve(provider, 'latest', method=method)
    except Error: latest = None
    rows = []
    for version in versions(provider):
        compatibility = terminal_policy.compatibility(provider, version)
        continuation = feature(provider, version)
        choices = methods(provider, version, method=method)
        rows.append(dict(version=version, recommended=version == terminal_policy.recommended(provider), latest=version == latest,
                         installable=compatibility['compatible'] and any(row['error'] is None for row in choices),
                         compatibility=compatibility, methods=choices, continuation=continuation))
    return dict(provider=provider, recommendedVersion=terminal_policy.recommended(provider), latestVersion=latest,
                policyRevision=policy.revision(), terminalPolicyRevision=terminal_policy.revision(),
                recipeRevision=recipe_revision(), versions=rows)


def feature(provider, version):
    result = terminal_policy.continuation(provider, version, checked=True)
    if result is not None:
        host = {'Linux': 'linux', 'Darwin': 'darwin', 'Windows': 'win32'}.get(platform.system(), platform.system())
        result['platformCompatible'] = host in result['platforms']
        result['available'] = result['compatible'] and result['platformCompatible']
        if result['compatible'] and not result['platformCompatible']:
            result['reason'] = 'Cross-environment continuation requires: ' + ', '.join(result['platforms'])
    return result


def parser(parser_type):
    cli = parser_type(prog='agent versions', description='List the CLI releases approved by this Agent Manager policy. No network lookup is performed.')
    from agent_manager.providers.installation.engine import catalog as providers, METHODS
    cli.add_argument('provider', choices=list(providers()))
    cli.add_argument('--method', choices=list(METHODS))
    cli.add_argument('--json', action='store_true')
    return cli


def command(args):
    import json
    result = catalog(args.provider, method=args.method)
    if args.json:
        print(json.dumps(result, indent=2)); return 0
    print(f'{args.provider}: recommended {result["recommendedVersion"]}; latest approved {result["latestVersion"] or "unavailable on this host/method"}')
    for row in result['versions']:
        labels = ', '.join(key for key in ('recommended', 'latest') if row[key])
        ready = ', '.join(item['id'] for item in row['methods'] if item['error'] is None) if row['compatibility']['compatible'] else ''
        feature = row['continuation']
        continuation = f'compatible ({feature["verification"]})' if feature and feature['available'] else 'unavailable'
        print(f'  {row["version"]}' + (f' [{labels}]' if labels else '') + f' | methods: {ready or "unavailable"} | cross-environment continuation: {continuation}')
        if row['compatibility']['reason']: print('    ' + row['compatibility']['reason'])
        for item in row['methods']:
            if item['error']: print(f'    {item["id"]}: {item["error"]}')
    return 0
