"""Public local environment leases for independently owned native processes."""
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil

from agent_manager.errors import Error, BusyError
from agent_manager.storage.files import private_dir
from .operations import inspect_environment
from .targets import describe


@dataclass(frozen=True)
class NativeEnvironmentLease:
    descriptor: dict
    env: dict = field(repr=False)
    pass_fds: tuple = field(repr=False)
    pass_handles: tuple = field(default=(), repr=False)

    @contextmanager
    def subprocess_options(self):
        """Keep only the lease handles open in the native child."""
        if os.name == 'nt':
            from agent_manager.storage.windows_guard import child_options
            with child_options(self.pass_handles) as options:
                yield options
        else:
            yield {'pass_fds': self.pass_fds}


@contextmanager
def lease(manager, environment_id, *, expected_revision=None):
    """Hold an environment against administrative mutation until close.

    Use subprocess_options() when creating the child. The child retains the
    guard if its owner dies. No credentials are read from disk.
    """
    if manager.provider not in ('codex', 'opencode'):
        raise Error('Native environment leases support Codex and OpenCode')
    import uuid
    try:
        if str(uuid.UUID(environment_id)) != environment_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise Error('Use an immutable environment UUID') from None
    private_dir(manager.lock_root)
    with native_guard(manager, environment_id) as (fds, handles):
        state = manager.state()
        target = describe(manager, environment_id, state=state)
        observed = inspect_environment(manager, environment_id, state=state)
        home = Path(target['home'])
        if not home.is_absolute() or not home.is_dir():
            raise Error('The selected native home is unavailable')
        executable = shutil.which(manager.config['executable'])
        if not executable:
            raise Error('The configured native executable is unavailable')
        from agent_manager.runtime.native.profile_environment import environment
        env = environment(manager.provider, os.environ)
        if manager.provider == 'codex':
            env['CODEX_HOME'] = str(home)
        else:
            from agent_manager.profiles.identities import for_environment
            if manager.active_runs(state, for_environment(state, environment_id)):
                raise Error('Active or unconfirmed native executions use this OpenCode environment')
            # Selected native accounts/configuration are authoritative. Do not let
            # ambient provider keys or OpenCode overrides retarget the server.
            env = {key: value for key, value in env.items()
                   if not key.startswith('OPENCODE_')
                   and key not in ('OPENAI_BASE_URL', 'ANTHROPIC_BASE_URL', 'GEMINI_API_BASE_URL',
                       'AZURE_OPENAI_ENDPOINT', 'CODEX_HOME', 'CODEX_SQLITE_HOME', 'AWS_PROFILE', 'AWS_DEFAULT_PROFILE')
                   and not any(word in key.upper() for word in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY', 'ACCESS_KEY', 'CREDENTIAL'))}
            from agent_manager.providers.opencode import environment as native_environment
            env.update(native_environment(manager, home))
        stable_target = {key: value for key, value in target.items() if key != 'aliases'}
        identity = dict(target=stable_target, environment_revision=observed['revision'],
            executable=str(Path(executable).absolute()), configuration=manager.config)
        if manager.provider == 'opencode':
            identity['native_paths'] = {key: env[key] for key in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME',
                'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'OPENCODE_CONFIG_DIR', 'OPENCODE_DB')}
        revision = 'sha256:' + hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()
        if expected_revision is not None and revision != expected_revision:
            raise Error('Environment or provider configuration changed; prepare again')
        descriptor = dict(schema='agent-manager/native-environment-1', provider=manager.provider,
            target=target, revision=revision, executable=identity['executable'],
            model=observed.get('model'), config=str(manager.configuration_source.resolve()),
            root=str(manager.root.parent.resolve()))
        yield NativeEnvironmentLease(descriptor, env, fds, handles)


@contextmanager
def native_guard(manager, environment_id):
    path = manager.lock_root / ('environment-' + environment_id + '.lock')
    with ExitStack() as stack:
        try:
            if os.name == 'nt':
                from agent_manager.storage.windows_guard import guard
                handle = stack.enter_context(guard(path, shared=manager.provider == 'codex'))
                fds, handles = (), (handle,)
            else:
                import fcntl
                stream = stack.enter_context(path.open('a'))
                os.chmod(stream.name, 0o600)
                mode = fcntl.LOCK_EX if manager.provider == 'opencode' else fcntl.LOCK_SH
                fcntl.flock(stream, mode | fcntl.LOCK_NB)
                fds, handles = (stream.fileno(),), ()
        except BlockingIOError:
            raise BusyError('environment-' + environment_id) from None
        yield fds, handles
