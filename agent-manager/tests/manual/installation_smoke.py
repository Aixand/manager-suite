"""Real vendor installations, invoked only inside installation.Containerfile."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def exercise(provider, method, report):
    from agent_manager import Manager
    from agent_manager.providers.installation import engine, plans, releases
    from agent_manager.providers.installation.software import fingerprint

    config = Path(os.environ['AGENT_MANAGER_CONFIG'])
    config.parent.mkdir(parents=True, exist_ok=True)
    require(not config.exists(), 'Expected a fresh container configuration')
    config.write_text('')
    manager = Manager(provider)
    spec = engine.catalog()[provider]
    receipt_path = engine.state_root() / 'installations' / (provider + '.json')

    def cli(*arguments, success=True):
        print('$ agent ' + ' '.join(arguments), flush=True)
        started = time.monotonic()
        result = subprocess.run(['agent', *arguments], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=600)
        print(result.stdout, end='', flush=True)
        print(result.stderr, end='', file=sys.stderr, flush=True)
        report['commands'].append(dict(arguments=list(arguments), exitCode=result.returncode,
                                       seconds=round(time.monotonic() - started, 2)))
        require((result.returncode == 0) == success, 'Unexpected command result: ' + ' '.join(arguments))
        return result.stdout

    def verify(version, selector=None):
        found = engine.discover(spec)
        require(len(found) == 1, 'Expected exactly one provider executable')
        installed = found[0]
        require(not installed.error and installed.version == version and installed.method == method,
                'Installed executable or method did not match the requested release')
        receipt = json.loads(receipt_path.read_text())
        require(receipt['version'] == receipt['resolvedVersion'] == version, 'Receipt version mismatch')
        require(receipt['method'] == method, 'Receipt method mismatch')
        if selector is not None:
            require(receipt['requestedSelector'] == selector, 'Receipt selector mismatch')
        for key in ('policyRevision', 'terminalPolicyRevision', 'recipeRevision'):
            require(receipt.get(key), 'Missing receipt policy revision: ' + key)
        require(Path(receipt['path']) == installed.path, 'Receipt executable path mismatch')
        return fingerprint(installed.path), receipt_path.read_bytes()

    require(not engine.discover(spec), 'Container already contains this provider')
    catalog = json.loads(cli('versions', provider, '--method', method, '--json'))
    choices = [row['version'] for row in catalog['versions'] if row['installable']]
    require(bool(choices), 'No approved release is installable for this case')
    oldest, recommended, latest = choices[-1], catalog['recommendedVersion'], catalog['latestVersion']
    require(recommended in choices and latest in choices, 'Recommended/latest release unavailable')
    report.update(host=dict(system=platform.system(), architecture=platform.machine()),
                  versions=dict(oldest=oldest, recommended=recommended, latest=latest),
                  policyRevision=catalog['policyRevision'], terminalPolicyRevision=catalog['terminalPolicyRevision'],
                  recipeRevision=releases.recipe_revision())

    cli('install', provider, '--method', method, '--version', 'recommended', '--dry-run')
    require(not receipt_path.exists() and not engine.discover(spec), 'Dry-run installed software or wrote a receipt')
    report['checks'].append('dry-run performs no installation')

    cli('install', provider, '--method', method, '--version', oldest, '--yes')
    before = verify(oldest, oldest)
    report['realVendorInstallations'] = True
    report['checks'].append('exact release and receipt')

    cli('install', provider, '--method', method, '--yes')
    require(verify(oldest) == before, 'Default install replaced a compatible release')
    report['checks'].append('default install preserves compatible software')

    cli('update', provider, '--version', 'recommended', '--yes')
    verify(recommended, 'recommended' if recommended != oldest else None)
    report['checks'].append('update to recommended release')

    cli('install', provider, '--method', method, '--version', 'recommended', '--reinstall', '--yes')
    verify(recommended, 'recommended')
    report['checks'].append('explicit reinstall')

    cli('update', provider, '--version', 'latest', '--yes')
    before = verify(latest, 'latest' if latest != recommended else None)
    report['checks'].append('latest resolves to an exact approved release')

    cli('update', provider, '--yes')
    require(verify(latest) == before, 'Default update replaced an equal/newer compatible release')
    report['checks'].append('default update preserves newer compatible software')

    print('$ shared installation planner: repair current release', flush=True)
    plan = plans.resolve(manager, 'repair')
    require(plan['targetVersion'] == latest and not plan['keepExisting'], 'Repair plan changed release')
    plans.execute(manager, plan)
    verify(latest, 'installed')
    report['checks'].append('repair preserves current release')

    cli('update', provider, '--version', oldest, '--yes')
    before = verify(oldest, oldest if oldest != latest else None)
    report['checks'].append('explicit downgrade' if oldest != latest else 'exact update keeps the sole approved release')

    cli('update', provider, '--version', '999999.0.0', '--yes', success=False)
    require(verify(oldest) == before, 'Rejected release modified the installation')
    require(not manager.state_path.exists(), 'Software installation created a profile registry')
    report['checks'].extend(['unapproved release rejected without replacement', 'no profile registry created'])
    report['finalVersion'] = oldest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('provider')
    parser.add_argument('method', choices=['installer'])
    args = parser.parse_args()
    require(os.environ.get('AGENT_INSTALL_SMOKE_CONTAINER') == '1'
            and (Path('/run/.containerenv').exists() or Path('/.dockerenv').exists())
            and os.geteuid() != 0 and Path.home() == Path('/home/agent-smoke'),
            'Run tests/manual/installation_containers.py; this script requires its disposable container')
    report = dict(provider=args.provider, method=args.method, status='failed', commands=[], checks=[],
                  realVendorInstallations=False, loginsInvoked=False, modelTurnsInvoked=False)
    started = time.monotonic()
    try:
        exercise(args.provider, args.method, report)
        report['status'] = 'passed'
    except Exception as error:
        report['error'] = str(error)
        traceback.print_exc()
    finally:
        report['seconds'] = round(time.monotonic() - started, 2)
        Path('/home/agent-smoke/results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
