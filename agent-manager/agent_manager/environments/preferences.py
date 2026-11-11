"""Manager preferences shared by every alias of an environment."""
from agent_manager.errors import Error
from agent_manager.profiles.identities import resolve
from agent_manager.storage.locks import locks
from agent_manager.environments.operations import check_revision


def set_model(manager, profile, model, *, expected_revision=None):
    target = manager.resolve_profile(profile)
    if manager.provider_spec.models.strategy == 'native-argument':
        from agent_manager.providers.arguments import model_id
    elif manager.provider_spec.home.opencode:
        from agent_manager.providers.opencode import model_id
    else:
        raise Error('Saved model selection is unavailable for this provider')
    if model is not None: model_id(model)
    with locks(manager.lock_root, 'registry', wait=True):
        state = manager.state(); resolve(state, target)
        check_revision(manager, target, expected_revision, state)
        if model is None: state.setdefault('models', {}).pop(target.environment_id, None)
        else: state.setdefault('models', {})[target.environment_id] = model
        manager.save(state)
    return 0
