"""Direct native instruction file editing, scoped to one environment."""
import hashlib
import os
from pathlib import Path

from agent_manager.errors import Error
from agent_manager.storage.files import atomic
from agent_manager.storage.locks import locks


def candidates(manager, profile):
    policy = manager.provider_spec.instructions
    if policy.strategy == 'disabled':
        raise Error(f'Native instruction editing is not configured for {manager.provider}')
    from agent_manager.environments.homes import base_home
    home = base_home(manager, profile)
    if policy.strategy == 'global': return [Path(path).expanduser() for path in policy.paths]
    if policy.strategy == 'opencode-config':
        from agent_manager.providers.opencode import environment
        home = Path(environment(manager, home)['OPENCODE_CONFIG_DIR'])
    return [home / name for name in (*policy.overrides, policy.filename)]


def native_path(manager, profile):
    paths = candidates(manager, profile)
    if manager.provider_spec.instructions.strategy == 'global':
        return next((path for path in paths if path.exists()), paths[0])
    return next((path for path in paths[:-1] if path.exists() and read(path).strip()), paths[-1])


def describe(manager, profile=None):
    target = manager.resolve_profile(profile)
    path = native_path(manager, target)
    return dict(provider=manager.provider, profile=str(target), environment_id=target.environment_id,
        scope='shared-native' if manager.provider_spec.instructions.strategy == 'global' or manager.provider_spec.home.shared else 'profile',
        path=str(path), exists=path.exists(), override=path.name in manager.provider_spec.instructions.overrides)


def read(path):
    try: return Path(path).read_bytes().decode('utf-8')
    except FileNotFoundError: return ''
    except UnicodeError: raise Error(f'Instruction file is not UTF-8: {path}') from None


def inspect(manager, profile=None):
    from agent_manager.storage.json import digest
    result = describe(manager, profile)
    result['content'] = read(result['path'])
    result['revision'] = digest([str(Path(result['path']).resolve()), result['exists'], result['content']])
    return result


def write(manager, text, profile=None, *, expected_revision=None):
    if not isinstance(text, str) or '\0' in text:
        raise Error('Instructions must be UTF-8 text without NUL characters')
    try: data = text.encode('utf-8')
    except UnicodeError: raise Error('Instructions must be valid UTF-8 text') from None
    target = manager.resolve_profile(profile)
    path = Path(describe(manager, target)['path']).resolve()
    import tempfile
    key = hashlib.sha256(str(path).encode()).hexdigest()
    root = Path(tempfile.gettempdir()) / ('agent-manager-instructions-' + str(os.getuid() if hasattr(os, 'getuid') else 'local'))
    # Instruction edits only serialize with other writes to this file. Native
    # sessions may keep using their loaded instructions while this file changes.
    with locks(root, key):
        current = inspect(manager, target)
        if Path(current['path']).resolve() != path or expected_revision is not None and current['revision'] != expected_revision:
            raise Error('Instruction file changed; refresh before editing')
        atomic(path, data)
        return inspect(manager, target)
