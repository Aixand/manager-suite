"""Shared discovery identity and bounded publication; schemas stay in their services."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil

from agent_manager.errors import Error
from agent_manager.storage.files import atomic

MAX_CACHE = 4 * 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _fingerprint(path):
    try:
        info = path.stat()
        return str(path), info.st_mtime_ns, info.st_size
    except OSError: return str(path), None, None


def identity(manager, target, cwd, *, policy, namespace, schema, inputs, dependencies=None):
    home = manager.home(target).resolve()
    program = shutil.which(manager.config['executable'])
    paths = [Path(__file__), *inputs]
    if program: paths.append(Path(program).resolve())
    # Stat allowlisted configuration only. Native credentials, database/session
    # activity and budget/model cache writes are deliberately excluded.
    paths.extend(home / name for name in ('config.toml', 'settings.json', 'opencode.json', 'opencode.jsonc',
        'config/opencode/opencode.json', 'config/opencode/opencode.jsonc', 'config.json'))
    for parent in (cwd, *list(cwd.parents)[:8]):
        paths.extend(parent / name for name in ('opencode.json', 'opencode.jsonc', '.opencode/opencode.json',
            '.opencode/opencode.jsonc', '.claude/settings.json', '.claude/settings.local.json', '.codex/config.toml'))
    if manager.provider == 'opencode':
        from agent_manager.providers.opencode import environment
        config = Path(environment(manager, home)['OPENCODE_CONFIG_DIR'])
        paths.extend(config / name for name in ('opencode.json', 'opencode.jsonc'))
        paths.append(Path(__file__).with_name('opencode.py'))
    login = manager.state().get('login_status', {}).get(target.environment_id, {})
    revision = digest(dict(schema=schema, policy=asdict(policy), program=program,
        files=[_fingerprint(path) for path in paths], home=str(home),
        config=manager.config, login=login.get('state'), dependencies=dependencies))
    key = digest(dict(environment=target.environment_id, cwd=str(cwd)))
    return home, revision, manager.root / namespace / target.environment_id / (key + '.json')


def write(path, value):
    encoded = (json.dumps(value, allow_nan=False) + '\n').encode()
    if len(encoded) > MAX_CACHE: raise Error('Discovery metadata exceeds the cache size limit')
    atomic(path, encoded)
    try:
        paths = sorted(path.parent.glob('*.json'), key=lambda item: item.stat().st_mtime, reverse=True)
        for old in paths[64:]: old.unlink(missing_ok=True)
    except FileNotFoundError: pass
