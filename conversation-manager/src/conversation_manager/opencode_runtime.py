"""The same native OpenCode qualification policy at discovery and dispatch."""
import os
import shutil
import subprocess
from functools import lru_cache

from .provider_policy import component, compatibility

QUALIFIED_VERSIONS = tuple(component('opencode')['tested_versions'])


def version_text(value):
    return value.strip().removeprefix('opencode v')


def version_error(version):
    return compatibility('opencode', version, backend='opencode-native')['reason']


@lru_cache(maxsize=32)
def _version(path, stamp, size):
    try:
        result = subprocess.run([path, '--version'], capture_output=True, timeout=5, check=False)
        return version_text(result.stdout.decode('utf-8', 'replace')) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def installation(executable):
    path = shutil.which(executable)
    if not path:
        return dict(available=False, version=None, reason=f'Install or configure {executable} on this machine.')
    try:
        info = os.stat(path)
        version = _version(path, info.st_mtime_ns, info.st_size)
    except OSError:
        version = None
    error = version_error(version)
    return dict(available=error is None, version=version, reason=error)
