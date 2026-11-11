"""Resource-neutral target discovery for independently installed tools.

Environment IDs are authoritative. Aliases are display metadata only; resolving a
retired ID must never fall back to an alias or create a native home.
"""
from copy import deepcopy

from agent_manager.errors import Error


def describe(manager, identifier, *, state=None):
    state = manager.state() if state is None else state
    if identifier not in state['environments']:
        raise Error('The original environment no longer exists')
    environment = state['environments'][identifier]
    return dict(
        schema='agent-manager/target-1', provider=manager.provider,
        registry=str(manager.state_path.resolve()), environment_id=identifier,
        home=environment['home'], ownership=environment['ownership'],
        aliases=sorted(key for key, row in state['profiles'].items()
                       if row['environment_id'] == identifier),
        capabilities=deepcopy(manager.provider_spec.capabilities()),
    )


def listing(manager):
    state = manager.state()
    return [describe(manager, identifier, state=state)
            for identifier in sorted(state['environments'])]
