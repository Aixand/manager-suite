"""Environment inspection, preference revisions and guarded retirement."""
import contextlib
import copy
import time
import hashlib
import json
from pathlib import Path
import shutil
import uuid
from agent_manager.errors import Error
from agent_manager.profiles.identities import environment_id, for_environment
from agent_manager.storage.locks import locks
from agent_manager.storage.files import private_dir

PREFERENCES = ('models', 'launch_args')


def revision(state, identifier):
    value = [state['environments'][identifier], {key: state.get(key, {}).get(identifier) for key in PREFERENCES}]
    return 'sha256:' + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def check_revision(manager, profile, expected, state=None):
    state = manager.state() if state is None else state
    identifier = environment_id(state, profile)
    if expected is not None and revision(state, identifier) != expected:
        raise Error('Environment changed; refresh before retrying')


def inspect_environment(manager, identifier, *, state=None):
    state = manager.state() if state is None else state
    if identifier not in state['environments']: raise Error('Unknown environment')
    return dict(provider=manager.provider, environment_id=identifier, **state['environments'][identifier],
        revision=revision(state, identifier),
        profiles=[dict(profile=key, profile_id=row['id']) for key, row in state['profiles'].items() if row['environment_id'] == identifier],
        model=state.get('models', {}).get(identifier),
        login=state.get('login_status', {}).get(identifier, {'state': 'unknown', 'verification': 'unchecked'}))


def listing(manager):
    state = manager.state()
    return [inspect_environment(manager, identifier, state=state) for identifier in sorted(state['environments'])]


def remove(manager, identifier, *, purge=False, expected_revision=None):
    # Environment -> registry is the same lock order as login and launch claims.
    from agent_manager.runtime.lifecycle import reconcile
    target = for_environment(manager.state(), identifier)
    with locks(manager.lock_root, 'environment-' + identifier, wait=True):
        reconcile(manager, target)
        with locks(manager.lock_root, 'registry', wait=True):
            state = manager.state()
            check_revision(manager, target, expected_revision, state)
            if any(row['environment_id'] == identifier for row in state['profiles'].values()):
                raise Error('Remove all profile aliases before retiring an environment')
            if manager.active_runs(state, target): raise Error('Active or unconfirmed runs still use this environment')
            transfers = state.get('opencode_continuation', {}).get('transfers', {})
            if any(item['environment_id'] == identifier for item in transfers.values()):
                raise Error('Retained OpenCode transfer journals still reference this environment')
            row = state['environments'][identifier]; home = Path(row['home'])
            if any(item.get('origin_environment_id') == identifier or Path(item['history_home']).resolve() == home.resolve()
                   or (item.get('rollout_path') and Path(item['rollout_path']).is_relative_to(home))
                   for item in state['sessions'].values()):
                raise Error('Retained sessions still reference this environment; its native data is preserved')
            if purge:
                expected = manager.managed_homes / identifier
                if row['ownership'] != 'managed' or home != expected.absolute():
                    raise Error('Only a verified Manager-owned environment home can be purged')
                if any(path.is_symlink() for path in (home, *home.parents)):
                    raise Error('Refusing to purge a home with symlink ancestors')
                if manager.provider == 'codex':
                    from agent_manager.runtime.native.codex_history import discover_home
                    stores = {Path(item['home']) for item in state['environments'].values()}
                    if any(item.rollout and item.rollout.is_relative_to(home.resolve())
                           for store in stores for item in discover_home(store, str(target), include_archived=True)):
                        raise Error('Native history still references this environment')
            from agent_manager.budgets.service import invalidate
            invalidate(manager, identifier)
            from agent_manager.models.service import invalidate as invalidate_models
            invalidate_models(manager, identifier)
            from agent_manager.integrations.service import invalidate as invalidate_integrations
            invalidate_integrations(manager, identifier)
            retired = None
            if purge and home.exists():
                retired = home.with_name('purging-' + uuid.uuid4().hex)
                home.rename(retired)
            del state['environments'][identifier]
            for key in (*PREFERENCES, 'login_status', 'session_routes'): state.get(key, {}).pop(identifier, None)
            try: manager.save(state)
            except BaseException:
                if retired is not None: retired.rename(home)
                raise
        if retired is not None: shutil.rmtree(retired)
    return dict(environment_id=identifier, forgotten=True, purged=purge, home=str(home), native_data_preserved=not purge)


@contextlib.contextmanager
def registration(manager, state, *, directory=None, model=None, preferences=None):
    """Prepare an environment within the caller's atomic registry transaction."""
    identifier = str(uuid.uuid4())
    managed = directory is None and not manager.provider_spec.home.shared
    home = manager.managed_homes / identifier if managed else Path(directory or manager.native).expanduser()
    if not managed and home.is_symlink(): raise Error('Connect requires a native directory, not a symlink')
    home = home.resolve()
    if directory is not None and not home.is_dir(): raise Error('Connect requires an existing native directory')
    if manager.provider_spec.home.shared and home != manager.native.resolve(): raise Error('This provider only supports its shared native home')
    existing = next((key for key, row in state['environments'].items() if Path(row['home']) == home), None)
    if existing:
        if model is not None or preferences is not None: raise Error('Connecting an existing environment cannot replace its preferences')
        yield existing
        return
    if manager.provider_spec.home.shared and state['environments']: raise Error('This provider supports one native environment')
    if model is not None:
        if not manager.provider_spec.models.saved_models: raise Error('Saved model selection is unavailable')
        from agent_manager.providers.arguments import model_id
        model_id(model)
    created = not home.exists()
    if managed or manager.provider_spec.home.shared: private_dir(home)
    state['environments'][identifier] = dict(home=str(home), ownership='managed' if managed else 'shared' if manager.provider_spec.home.shared else 'external', created_at=time.time())
    if model is not None: state.setdefault('models', {})[identifier] = model
    for key, value in (preferences or {}).items():
        if value is not None: state.setdefault(key, {})[identifier] = copy.deepcopy(value)
    try: yield identifier
    except BaseException:
        # Preserve a home if publication succeeded before an error was reported.
        try: unpublished = identifier not in manager.state()['environments']
        except (Error, OSError): unpublished = False
        if managed and created and unpublished: shutil.rmtree(home)
        raise
