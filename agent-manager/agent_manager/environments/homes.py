"""Environment-owned native homes, with scoped views for a single launch."""
import contextlib
import contextvars
from pathlib import Path
from agent_manager.profiles.identities import resolve, environment

_selected = contextvars.ContextVar('agent_manager_launch_homes', default={})


def base_home(manager, profile, state=None):
    state = manager.state() if state is None else state
    return Path(environment(state, profile)['home'])


def current_home(manager, profile, state=None):
    state = manager.state() if state is None else state
    target = resolve(state, profile)
    return _selected.get().get((str(manager.root.resolve()), target.environment_id), base_home(manager, target, state))


@contextlib.contextmanager
def using_home(manager, profile, home):
    target = manager.resolve_profile(profile)
    token = _selected.set(_selected.get() | {(str(manager.root.resolve()), target.environment_id): Path(home)})
    try: yield
    finally: _selected.reset(token)


def profile_homes(manager, profile, state=None):
    return [base_home(manager, profile, state)]


def home_details(manager, profile, state=None):
    home = base_home(manager, profile, state)
    scope = 'shared' if manager.provider_spec.home.shared else 'environment'
    return dict(home=str(home), homeBinding=scope, homeScope=scope, settingsScope=scope, historyScope=scope)
