"""Registry identities. Alias names never identify execution environments."""
from pathlib import Path
import uuid
from agent_manager.errors import Error

SCHEMA = 3


class Target(str):
    """Captured execution target; its string value is only a display name.

    Never reconstruct a target from its display name after accepting work. Persist
    environment_id and profile_id explicitly across process boundaries.
    """
    __slots__ = ('profile_id', 'environment_id')

    def __new__(cls, name, profile_id, environment_id):
        value = super().__new__(cls, name)
        object.__setattr__(value, 'profile_id', profile_id)
        object.__setattr__(value, 'environment_id', environment_id)
        return value

    def __setattr__(self, key, value):
        raise AttributeError('Execution targets are immutable')

    def __delattr__(self, key):
        raise AttributeError('Execution targets are immutable')

    def __reduce__(self):
        return Target, (str(self), self.profile_id, self.environment_id)


def empty():
    return dict(schema=SCHEMA, environments={}, profiles={}, default_profile_id=None, runs={}, sessions={})


def _uuid(value):
    try:
        if str(uuid.UUID(value)) != value: raise ValueError()
    except (ValueError, TypeError, AttributeError): raise Error('Invalid registry identity') from None


def validate(state):
    if not isinstance(state, dict) or state.get('schema') != SCHEMA:
        raise Error('Unsupported registry; create a fresh version-three registry and explicitly connect existing native homes. Native files have not been changed.')
    if any(key in state for key in ('agents', 'auths', 'auth_ids', 'sdk_credentials', 'claude_accounts')):
        raise Error('Saved-auth registries are not supported')
    from agent_manager.validation import name
    for key in ('environments', 'profiles', 'runs', 'sessions'):
        if not isinstance(state.get(key), dict): raise Error('Invalid environment registry')
    homes = set()
    for identifier, row in state['environments'].items():
        _uuid(identifier)
        if not isinstance(row, dict) or set(row) != {'home', 'ownership', 'created_at'} or row.get('ownership') not in ('managed', 'external', 'shared'):
            raise Error('Invalid environment ownership')
        home = row.get('home')
        if not isinstance(home, str) or not Path(home).is_absolute() or home in homes:
            raise Error('Invalid or duplicate environment home')
        homes.add(home)
    ids, names = set(), set()
    for key, row in state['profiles'].items():
        name(key)
        if not isinstance(row, dict) or set(row) != {'id', 'environment_id', 'created_at'}:
            raise Error('Invalid profile alias')
        _uuid(row['id'])
        if row['id'] in ids or key.casefold() in names or row['environment_id'] not in state['environments']:
            raise Error('Invalid or duplicate profile alias')
        ids.add(row['id']); names.add(key.casefold())
    if state.get('default_profile_id') is not None and state['default_profile_id'] not in ids:
        raise Error('Default profile no longer exists')
    for row in state['runs'].values():
        if not isinstance(row, dict) or row.get('environment_id') not in state['environments'] or any(key in row for key in ('auth', 'auth_id', 'login_generation')):
            raise Error('Invalid environment run identity')
    for row in state['sessions'].values():
        if not isinstance(row, dict) or row.get('origin_environment_id') not in state['environments'] or 'origin_profile_id' in row:
            raise Error('Invalid native session origin')
    routes = state.get('session_routes', {})
    if not isinstance(routes, dict) or routes.keys() - state['environments'].keys():
        raise Error('Invalid native session routes')
    for values in routes.values():
        if not isinstance(values, dict): raise Error('Invalid native session routes')
        for native_id, reference in values.items():
            if reference not in state['sessions'] or state['sessions'][reference]['native_id'] != native_id:
                raise Error('Invalid native session route identity')
    for key in ('models', 'launch_args', 'login_status'):
        if not isinstance(state.get(key, {}), dict) or set(state.get(key, {})) - state['environments'].keys():
            raise Error('Invalid environment preferences')
    from agent_manager.providers.saved_args import validate as validate_args
    for values in state.get('launch_args', {}).values(): validate_args(values)
    from agent_manager.profiles.opencode_copies import validate_state
    validate_state(state)
    return state


def label(state, identifier):
    return next((key for key, row in state['profiles'].items() if row['id'] == identifier), None)


def for_environment(state, identifier, *, profile_id=None, display=None):
    if identifier not in state['environments']: raise Error('The original environment no longer exists')
    current = label(state, profile_id)
    if current is None:
        current = next((key for key, row in state['profiles'].items() if row['environment_id'] == identifier), None)
    return Target(current or display or identifier, profile_id if profile_id is not None else
        (state['profiles'][current]['id'] if current else None), identifier)


def resolve(state, profile=None):
    if isinstance(profile, Target):
        if profile.environment_id not in state['environments']: raise Error('The original environment no longer exists')
        return profile
    if profile is None: profile = label(state, state.get('default_profile_id'))
    actual = next((key for key in state['profiles'] if isinstance(profile, str) and key.casefold() == profile.casefold()), None)
    if actual is None: raise Error('Choose an existing profile or set an explicit default profile')
    row = state['profiles'][actual]
    return Target(actual, row['id'], row['environment_id'])


def environment_id(state, profile):
    return resolve(state, profile).environment_id


def environment(state, profile):
    return state['environments'][environment_id(state, profile)]


def home_id(manager, state, home):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(Path(home).resolve())))


def bind(manager, state, row, profile, home=None):
    target = resolve(state, profile)
    row.update(profile_id=target.profile_id, environment_id=target.environment_id)
    if home: row['home_id'] = home_id(manager, state, home)
    return row


def resolve_record(state, row):
    result = dict(row)
    field = 'agent' if 'agent' in row else 'profile'
    identifier = row.get('environment_id')
    if identifier is None: raise Error('Unsupported retained record identity')
    if identifier in state['environments']:
        result[field] = for_environment(state, identifier, profile_id=row.get('profile_id'), display=row.get(field))
    else:
        result[field] = Target(row.get(field, identifier), row.get('profile_id'), identifier)
    return result


def snapshot(manager):
    state = manager.state()
    return dict(schema=SCHEMA, profiles=[dict(profile=key, **row) for key, row in state['profiles'].items()],
        environments=state['environments'], default_profile_id=state['default_profile_id'])


def storage_key(root, selector):
    from agent_manager.storage.registry import read_state
    state = validate(read_state(Path(root) / 'state.json'))
    return environment_id(state, selector)
