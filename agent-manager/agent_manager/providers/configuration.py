"""Headless configuration inspection and explicit refresh for long-lived clients."""
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace
from functools import wraps
import inspect as introspection


_launches = ContextVar('agent_manager_launch_configuration', default={})


def current(manager): return _launches.get().get(manager)


@contextmanager
def freeze(manager):
    """A refresh affects future launches, never an in-flight launch's settings."""
    if current(manager) is not None:
        yield
        return
    with manager._configuration_lock:
        snapshot = dict(spec=replace(manager.provider_spec, settings=deepcopy(manager.config)), native=manager.native)
    token = _launches.set({**_launches.get(), manager: snapshot})
    try: yield
    finally: _launches.reset(token)


def scoped(function):
    if introspection.iscoroutinefunction(function):
        @wraps(function)
        async def asynchronous(owner, *args, **kwargs):
            with freeze(getattr(owner, 'manager', owner)):
                return await function(owner, *args, **kwargs)
        return asynchronous
    @wraps(function)
    def synchronous(owner, *args, **kwargs):
        with freeze(getattr(owner, 'manager', owner)):
            return function(owner, *args, **kwargs)
    return synchronous


def refresh(manager):
    from agent_manager.providers.config import load_registry
    registry = load_registry(manager.configuration_source, allow_missing=True)
    resolved = registry.resolve(manager.provider)
    native = Path(resolved.settings['native_home']).expanduser().absolute()
    if resolved.home.opencode and resolved.settings['native_home'] == '~/.local/share/opencode':
        import os
        native = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'opencode'
    with manager._configuration_lock:
        manager.provider_spec, manager.config, manager.native = resolved, resolved.settings, native
        manager.config_path = registry.config_path
    return manager


def inspect(manager, profile=None, *, effective=False, refresh_config=True):
    if refresh_config: refresh(manager)
    from agent_manager.providers import saved_args, native_instructions
    state = manager.state()
    target = manager.resolve_profile(profile) if profile is not None else None
    if target is None and effective and state.get('default_profile_id'):
        target = manager.resolve_profile()
    provider = saved_args.configure(manager, 'list')
    scoped = saved_args.configure(manager, 'list', profile=target) if target is not None else None
    layers = dict(provider_run=list(manager.config.get('run_args', [])), provider=provider,
        profile=scoped, effective=(manager.saved_launch_args(target) if target is not None else
            list(manager.config.get('run_args', [])) + provider['args']))
    instructions = dict(supported=manager.provider_spec.instructions.strategy != 'disabled')
    if instructions['supported']:
        def source(profile=None):
            row = native_instructions.inspect(manager, profile)
            row.pop('content')
            return row
        if target is not None: instructions['profile'] = source(target)
        instructions['scope'] = 'native-environment'
    model = state.get('models', {}).get(target.environment_id) if target is not None else None
    result = dict(schema='agent-manager/configuration-1', provider=manager.provider,
        profile=str(target) if target is not None else None,
        environment_id=target.environment_id if target is not None else None,
        path=str(manager.configuration_source), runtime=manager.config['runtime'],
        executable=manager.config['executable'], arguments=layers, instructions=instructions,
        model=model, native_model=None, native_defaults_resolved=False)
    if target is not None and effective:
        from agent_manager.providers.arguments import effective_model
        result['native_model'] = effective_model(manager, target)
    return result
