"""Explicit default routing, captured by environment identity and core revision."""
from agent_manager.environments.operations import revision
from agent_manager.errors import Error
from agent_manager.profiles.identities import resolve


def verify(manager, decision):
    state = manager.state()
    identifier = decision['environment_id']
    if identifier not in state['environments'] or revision(state, identifier) != decision['environment_revision']:
        raise Error('Selected environment changed; select again')


def select(manager, *, eligible_profiles=None, model=None):
    state = manager.state()
    profile = resolve(state)
    if eligible_profiles is not None and profile not in eligible_profiles:
        raise Error('Default profile is not eligible; choose an execution profile explicitly')
    return dict(profile=profile, profile_id=profile.profile_id, environment_id=profile.environment_id,
                environment_revision=revision(state, profile.environment_id), model=model,
                reason='Explicit default profile')
