"""Qualify a packaged SDK against offline providers and an isolated Undercurrent."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from dev import package_source

PROJECT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uc-bin', type=Path, required=True, help='Directory containing Undercurrent binaries')
    parser.add_argument('--runtime-source', type=Path, default=PROJECT.parent.parent / 'aixand-libraries/local-runtime', help='Local runtime package source (default: ../../aixand-libraries/local-runtime)')
    parser.add_argument('--filesystem-source', type=Path, default=PROJECT.parent.parent / 'aixand-libraries/filesystem', help='Filesystem package source (default: ../../aixand-libraries/filesystem)')
    parser.add_argument('--undercurrent-source', type=Path, help='Install this local aixand-undercurrent-sdk instead of the index version')
    parser.add_argument('--output', type=Path, help='New directory for the environment, logs and results (default: temporary directory)')
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('This terminal fixture qualification currently requires POSIX')
    binaries = args.uc_bin.expanduser().resolve()
    for name in ('uc-daemon', 'uc-session-host', 'uc-python-bridge'):
        if not os.access(binaries / name, os.X_OK):
            parser.error(f'Missing executable: {binaries / name}')
    sources = []
    for source in (args.filesystem_source, args.runtime_source, args.undercurrent_source):
        if source is not None:
            source = source.expanduser().resolve()
            if not (source / 'pyproject.toml').is_file():
                parser.error(f'Not a Python package source: {source}')
            sources.append(str(source))
    if args.output is None:
        output = Path(tempfile.mkdtemp(prefix='agent-sdk-qualification-'))
    else:
        output = args.output.expanduser().resolve()
        output.mkdir(parents=True)  # Refuse to overwrite any previous run.
    print(f'Qualification artifacts: {output}', flush=True)
    snapshot = package_source(output / 'package')
    subprocess.run([sys.executable, '-m', 'venv', str(output / 'venv')], check=True)
    python = output / 'venv/bin/python'
    with (output / 'install.log').open('w') as log:
        try:
            subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                            f'{snapshot}[sdk]', *sources], check=True, cwd=output, stdout=log, stderr=log)
        except subprocess.CalledProcessError:
            print(f'Package installation failed; inspect {output / "install.log"}', file=sys.stderr)
            raise
    for source, destination in (
        (PROJECT / 'tests/qualification/sdk_consumer.py', 'consumer.py'),
        (PROJECT / 'tests/qualification/sdk_provider.py', 'codex'),
        (PROJECT / 'examples/sdk_consumer.py', 'example.py'),
    ):
        shutil.copy2(source, output / destination)
    (output / 'codex').chmod(0o700)
    with (output / 'qualification.log').open('w') as log:
        result = subprocess.run([str(python), '-I', str(output / 'consumer.py'), '--uc-bin', str(binaries)],
                                cwd=output, stdout=log, stderr=subprocess.STDOUT)
    print((output / 'qualification.log').read_text(), end='')
    if result.returncode:
        raise SystemExit(result.returncode)
    print(f'PASS: {output / "results.json"}')


if __name__ == '__main__':
    main()
