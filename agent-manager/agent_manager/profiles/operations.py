"""Named aliases over stable native environments."""
import copy
import hashlib
import json
import time
import uuid
from agent_manager.errors import Error
from agent_manager.storage.locks import locks
from agent_manager.validation import name
from agent_manager.profiles.identities import resolve, label, for_environment
from agent_manager.environments.operations import PREFERENCES


def available_name(manager, value, previous=None, state=None):
    name(value)
    state = manager.state() if state is None else state
    if any(key.casefold() == value.casefold() and key != previous for key in state["profiles"]):
        raise Error("Profile name already exists")


def revision(state, profile):
    target = resolve(state, profile)
    current = label(state, target.profile_id)
    if current is None: raise Error("The profile alias no longer exists")
    value = [current, state['profiles'][current]]
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def check_alias_revision(manager, profile, expected, state=None):
    state = manager.state() if state is None else state
    current = revision(state, profile)
    if expected is not None and current != expected: raise Error("Profile alias changed; refresh before retrying")


def inspect_profile(manager, profile):
    state = manager.state(); target = resolve(state, profile)
    current = label(state, target.profile_id)
    if current is None: raise Error("The profile alias no longer exists")
    row = state['environments'][target.environment_id]
    from agent_manager.environments.homes import home_details
    from agent_manager.environments.operations import revision as environment_revision
    return dict(provider=manager.provider, profile=current, profile_id=target.profile_id, environment_id=target.environment_id,
        home=row['home'], ownership=row['ownership'], managed=row['ownership'] == 'managed',
        revision=revision(state, target), environment_revision=environment_revision(state, target.environment_id),
        model=state.get('models', {}).get(target.environment_id), capabilities=manager.provider_spec.capabilities(),
        preferred=target.profile_id == state.get('default_profile_id'), scopes=home_details(manager, target, state),
        login=state.get('login_status', {}).get(target.environment_id, {'state': 'unknown', 'verification': 'unchecked'}))


def _alias(state, profile, identifier):
    state['profiles'][profile] = dict(id=str(uuid.uuid4()), environment_id=identifier, created_at=time.time())
    return resolve(state, profile)


def _publish(manager, state, profile, **options):
    from agent_manager.environments.operations import registration
    with registration(manager, state, **options) as identifier:
        target = _alias(state, profile, identifier)
        manager.save(state)
    return target


def create_profile(manager, profile, *, directory=None, model=None):
    with locks(manager.lock_root, 'registry', wait=True):
        state = manager.state(); available_name(manager, profile, state=state)
        target = _publish(manager, state, profile, directory=directory, model=model)
    return inspect_profile(manager, target)


def alias_profile(manager, new_name, profile=None, *, environment_id=None):
    if (profile is None) == (environment_id is None): raise Error("Choose exactly one source profile or environment ID")
    target = manager.resolve_profile(profile) if environment_id is None else for_environment(manager.state(), environment_id)
    with locks(manager.lock_root, 'registry', wait=True):
        state = manager.state(); resolve(state, target); available_name(manager, new_name, state=state)
        result = _alias(state, new_name, target.environment_id); manager.save(state)
    return inspect_profile(manager, result)


def rename_profile(manager, profile, new_name, *, expected_revision=None):
    target = manager.resolve_profile(profile)
    with locks(manager.lock_root, 'registry', wait=True):
        state = manager.state(); check_alias_revision(manager, target, expected_revision, state)
        current = label(state, target.profile_id)
        if current is None: raise Error('The profile alias no longer exists')
        available_name(manager, new_name, previous=current, state=state)
        state['profiles'][new_name] = state['profiles'].pop(current); manager.save(state)
        result = resolve(state, new_name)
    return inspect_profile(manager, result)


def delete_profile(manager, profile, *, expected_revision=None):
    target = manager.resolve_profile(profile)
    with locks(manager.lock_root, 'registry', wait=True):
        state = manager.state(); check_alias_revision(manager, target, expected_revision, state)
        current = label(state, target.profile_id)
        if current is None: raise Error('The profile alias no longer exists')
        del state['profiles'][current]
        if state.get('default_profile_id') == target.profile_id: state['default_profile_id'] = None
        manager.save(state)
    return dict(deleted=True, profile_id=target.profile_id, environment_id=target.environment_id, native_data_preserved=True)


def duplicate_profile(manager, profile, new_name, *, expected_revision=None):
    target = manager.resolve_profile(profile)
    with locks(manager.lock_root, 'registry', wait=True):
        state = manager.state(); check_alias_revision(manager, target, expected_revision, state)
        available_name(manager, new_name, state=state)
        if manager.provider_spec.home.shared: raise Error('A shared native environment cannot be duplicated')
        source = {key: copy.deepcopy(state.get(key, {}).get(target.environment_id)) for key in PREFERENCES}
        result = _publish(manager, state, new_name, preferences=source)
    return inspect_profile(manager, result)


class Management:
    def __init__(self, manager): self.manager = manager

    def execute(self, tool, arguments, *, request_id=None):
        from agent_manager.interfaces.management_contract import validate
        if not isinstance(arguments, dict): raise Error('Invalid profile management tool arguments')
        arguments = dict(arguments)
        arguments.setdefault('provider', self.manager.provider)
        validate(tool, arguments)
        if arguments.get('provider') != self.manager.provider: raise Error('Provider does not match this manager')
        request_id = request_id or arguments.get('requestId')
        if request_id is None or tool in ("list_profiles", "get_profile", "check_profile", "list_environments", "get_environment", 'get_budget', 'list_budgets', 'get_models', 'get_integrations', 'get_configuration', 'get_launch_arguments', 'get_native_instructions', 'check_readiness'):
            return self._execute(tool, arguments)
        from agent_manager.storage.files import atomic
        identifier = str(uuid.UUID(request_id))
        path = self.manager.root / "profile-operations" / (identifier + ".json")
        signature = hashlib.sha256(json.dumps([tool, arguments], sort_keys=True).encode()).hexdigest()
        with locks(self.manager.lock_root, "profile-operation-" + identifier, wait=True):
            if path.exists():
                prior = json.loads(path.read_text())
                if prior["signature"] != signature: raise Error("Request ID was reused with different arguments")
                if prior["status"] == "completed": return prior["result"]
                raise Error("Profile operation completion is unconfirmed; inspect the profile before retrying")
            atomic(path, json.dumps(dict(signature=signature, status="submitting")).encode())
            result = self._execute(tool, arguments)
            atomic(path, json.dumps(dict(signature=signature, status="completed", result=result)).encode())
            return result

    def _execute(self, tool, arguments):
        a = dict(arguments); a.pop("provider", None); a.pop("requestId", None)
        profile = a.pop("profile", None)
        expected = a.pop("expectedRevision", None)
        if profile is not None: profile = self.manager.resolve_profile(profile)
        if tool == 'get_configuration': return {'configuration': self.manager.configuration(profile, effective=a.get('effective', False), refresh=a.get('refresh', True))}
        if tool == 'get_launch_arguments': return {'arguments': self.manager.launch_arguments(profile)}
        if tool == 'set_launch_arguments': return {'arguments': self.manager.set_launch_arguments(a['args'], profile, expected_revision=expected)}
        if tool == 'get_native_instructions': return {'instructions': self.manager.native_instructions(profile)}
        if tool == 'set_native_instructions': return {'instructions': self.manager.set_native_instructions(a['text'], profile, expected_revision=expected)}
        if tool == 'check_readiness': return {'readiness': self.manager.readiness(profile, refresh=a.get('refresh', False))}
        if tool in ('list_environments', 'get_environment', 'forget_environment', 'purge_environment'):
            from agent_manager.environments.operations import listing, inspect_environment, remove
            if tool == 'list_environments': return {'environments': listing(self.manager)}
            if tool == 'get_environment': return {'environment': inspect_environment(self.manager, a['environmentId'])}
            return remove(self.manager, a['environmentId'], purge=tool == 'purge_environment', expected_revision=expected)
        if tool == 'list_providers':
            from agent_manager.providers.config import load_registry
            registry = load_registry(self.manager.config_path)
            return {'providers': [dict(provider=provider, **registry.resolve(provider).capabilities()) for provider in registry.definitions]}
        if tool == "list_profiles":
            from agent_manager.profiles.listing import list_profiles
            rows = list_profiles(self.manager)
            if a.get('query'): rows = [row for row in rows if a['query'].casefold() in row['profile'].casefold()]
            return {"profiles": rows}
        if tool == "get_profile": return {"profile": inspect_profile(self.manager, profile)}
        if tool == 'get_integrations': return {'integrations': self.manager.integrations(profile, cwd=a.get('cwd'), refresh=a.get('refresh', False), cached=a.get('cached', False))}
        if tool == 'get_budget': return {'budget': self.manager.budget(profile, refresh=a.get('refresh', False), cached=a.get('cached', False), cwd=a.get('cwd'), model=a.get('model'))}
        if tool == 'get_models': return {'catalog': self.manager.models(profile, cwd=a.get('cwd'), refresh=a.get('refresh', False), cached=a.get('cached', False))}
        if tool == 'list_budgets': return {'budgets': self.manager.budgets(refresh=a.get('refresh', False), cached=a.get('cached', False), cwd=a.get('cwd'), model=a.get('model'))}
        if tool == "alias_profile": result = alias_profile(self.manager, a["name"], profile, environment_id=a.get("environmentId"))
        elif tool == "create_profile": result = create_profile(self.manager, a["name"], model=a.get("model"))
        elif tool == "connect_profile": result = create_profile(self.manager, a["name"], directory=a["directory"])
        elif tool == "rename_profile": result = rename_profile(self.manager, profile, a["newName"], expected_revision=expected)
        elif tool == "duplicate_profile": result = duplicate_profile(self.manager, profile, a["newName"], expected_revision=expected)
        elif tool == "delete_profile": return delete_profile(self.manager, profile, expected_revision=expected)
        elif tool == "set_default_profile":
            self.manager.set_default(profile, expected_revision=expected); result = inspect_profile(self.manager, profile)
        elif tool == "check_profile":
            from agent_manager.environments.login import status
            status(self.manager, profile); result = inspect_profile(self.manager, profile)
        else: raise Error("Unknown profile operation")
        return {"profile": result}
