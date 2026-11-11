"""Run real installation checks in disposable rootless Podman containers.

Host requirements: Python 3.11+, working rootless Podman and internet access.
No source, home, credentials, runtime socket or output directory is host-mounted.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid

PROJECT = Path(__file__).resolve().parents[2]
MANUAL = Path(__file__).resolve().parent


def copy_source(source, destination):
    """Copy package sources only; reject redirected paths and omit local state."""
    if source.is_symlink():
        raise RuntimeError('Refusing a source symlink: ' + str(source))
    for path in sorted(source.rglob('*')):
        if any(part == '__pycache__' or part.startswith('.') for part in path.relative_to(source).parts):
            continue
        if path.is_symlink():
            raise RuntimeError('Refusing a source symlink: ' + str(path))
        if path.is_file() and path.suffix in ('.py', '.toml', '.txt'):
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def prepare(context, runtime_source, filesystem_source):
    for source, package, name in [(PROJECT, 'agent_manager', 'agent-manager'),
                                  (runtime_source, 'src/aixand_local_runtime', 'aixand-lib-local-runtime'),
                                  (filesystem_source, 'src/aixand_filesystem', 'aixand-lib-filesystem')]:
        if not (source / package).is_dir():
            raise RuntimeError('Missing package source: ' + str(source / package))
        if not (source / package).resolve().is_relative_to(source.resolve()):
            raise RuntimeError('Package source is redirected outside its project')
        target = context / name
        target.mkdir(parents=True)
        for filename in ('pyproject.toml', 'README.md', 'LICENSE'):
            path = source / filename
            if path.is_symlink():
                raise RuntimeError('Refusing a metadata symlink: ' + str(path))
            shutil.copyfile(path, target / filename)
        copy_source(source / package, target / package)
    shutil.copyfile(MANUAL / 'installation.Containerfile', context / 'Containerfile')
    shutil.copyfile(MANUAL / 'installation_smoke.py', context / 'installation_smoke.py')


def recipes():
    return tomllib.loads((PROJECT / 'agent_manager/providers/installation/install_catalog.toml').read_text())['agents']


def matrix(providers, method):
    data = recipes()
    return [dict(provider=name, method=choice) for name in dict.fromkeys(providers or data)
            for choice in ['installer'] if method == choice]


def logged(command, path, timeout):
    print('$ ' + ' '.join(str(arg) for arg in command), flush=True)
    print('Log: ' + str(path), flush=True)
    with path.open('w') as output:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output,
                                stderr=subprocess.STDOUT, text=True, timeout=timeout)
    if result.returncode:
        print(path.read_text(errors='replace')[-8000:], file=sys.stderr, flush=True)
    return result.returncode


def cleanup(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if result.returncode:
            return result.stderr.strip() or 'Cleanup command failed'
        return None
    except (OSError, subprocess.TimeoutExpired) as error:
        return str(error)


def run_case(image, case, output, timeout):
    label = case['provider'] + '-' + case['method']
    name = 'agent-installation-smoke-' + uuid.uuid4().hex
    row = dict(case, status='failed', log=label + '.log')
    try:
        code = logged(['podman', 'run', '--name', name, '--pull=never', '--http-proxy=false',
                       '--cap-drop=all', '--security-opt=no-new-privileges', '--pids-limit=512',
                       image, case['provider'], case['method']], output / row['log'], timeout)
        findings = output / (label + '.json')
        copied = subprocess.run(['podman', 'cp', name + ':/home/agent-smoke/results.json', str(findings)],
                                capture_output=True, text=True, timeout=30)
        if copied.returncode == 0:
            row['findings'] = findings.name
            result = json.loads(findings.read_text())
            if code == 0 and result.get('status') == 'passed' and all(result.get(key) == case[key] for key in case):
                row['status'] = 'passed'
            else:
                row['error'] = result.get('error', 'Container did not complete all checks')
        else:
            row['error'] = 'No findings were recovered: ' + copied.stderr.strip()
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        row['error'] = str(error)
    finally:
        problem = cleanup(['podman', 'rm', '--force', '--ignore', name])
        if problem:
            row.update(status='failed', cleanupError=problem)
    print(label + ': ' + row['status'], flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', action='append', choices=list(recipes()))
    parser.add_argument('--method', choices=['installer'], default='installer')
    parser.add_argument('--output', type=Path, help='New directory for logs and JSON results; default: a temporary directory')
    parser.add_argument('--runtime-source', type=Path, default=PROJECT.parent.parent / 'aixand-libraries/local-runtime')
    parser.add_argument('--filesystem-source', type=Path, default=PROJECT.parent.parent / 'aixand-libraries/filesystem')
    parser.add_argument('--prepare-only', action='store_true', help='Prepare and retain the build context without invoking Podman')
    parser.add_argument('--timeout', type=int, default=1800, help='Seconds allowed per container (default: 1800)')
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error('--timeout must be positive')
    cases = matrix(args.provider, args.method)
    if not cases:
        parser.error('No supported provider/method combination selected')
    output = args.output.expanduser().absolute() if args.output else Path(tempfile.mkdtemp(prefix='agent-installation-smoke-'))
    if args.output:
        output.mkdir(parents=True, exist_ok=False, mode=0o700)
    context = output / 'context'
    report = dict(status='blocked', platform='linux-container', cases=[], selectedCases=cases,
                  hostBindMounts=False, hostCredentialsForwarded=False)
    image = 'localhost/agent-installation-smoke:' + uuid.uuid4().hex
    build_attempted, code = False, 1
    try:
        prepare(context, args.runtime_source.expanduser().resolve(), args.filesystem_source.expanduser().resolve())
        if args.prepare_only:
            report['status'] = 'prepared'
            print('Prepared build context: ' + str(context), flush=True)
            code = 0
        else:
            if os.name != 'posix' or os.geteuid() == 0:
                raise RuntimeError('Run as your normal user with rootless Podman')
            if not shutil.which('podman'):
                raise RuntimeError('Podman is not installed')
            runtime = subprocess.run(['podman', 'info', '--format', 'json'],
                                     capture_output=True, text=True, timeout=30)
            (output / 'preflight.log').write_text(runtime.stderr + runtime.stdout)
            if runtime.returncode:
                raise RuntimeError('Rootless Podman is unavailable; see preflight.log. No vendor tests ran.')
            info = json.loads(runtime.stdout)
            if info.get('host', {}).get('security', {}).get('rootless') is not True:
                raise RuntimeError('The selected Podman connection must be rootless')
            report['status'] = 'failed'
            build_attempted = True
            if logged(['podman', 'build', '--http-proxy=false', '--tag', image, str(context)], output / 'build.log', 1800):
                raise RuntimeError('Container image build failed; see build.log. No vendor tests ran.')
            for case in cases:
                report['cases'].append(run_case(image, case, output, args.timeout))
            report['status'] = 'passed' if all(row['status'] == 'passed' for row in report['cases']) else 'failed'
            code = 0 if report['status'] == 'passed' else 1
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        report['error'] = str(error)
        print(str(error), file=sys.stderr)
    except KeyboardInterrupt:
        report.update(status='interrupted', error='Interrupted by user')
        code = 130
    finally:
        if build_attempted:
            problem = cleanup(['podman', 'image', 'rm', '--force', '--ignore', image])
            if problem:
                report['imageCleanupError'] = problem
                report['status'] = 'failed'
                code = code or 1
        if not args.prepare_only:
            shutil.rmtree(context, ignore_errors=True)
        (output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
        print('Results: ' + str(output / 'results.json'), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
