"""Choose a named environment by current allowance; never change a native login."""
from dataclasses import dataclass

from agent_manager.budgets.ranking import Ineligible, Score, comparison_key, score
from agent_manager.budgets.service import inspect_environments, matches_observation, selection_policy
from agent_manager.environments.operations import revision
from agent_manager.errors import Error
from agent_manager.profiles.identities import Target, resolve
from agent_manager.profiles.operations import inspect_profile
from agent_manager.providers.arguments import effective_model


class SelectionUnavailable(Error):
    """No allowance-qualified target; callers must explicitly choose a fallback."""
    code = "selection_unavailable"


@dataclass(frozen=True)
class Decision:
    target: Target
    environment_revision: str
    reason: str
    model: str | None = None
    budget: dict | None = None
    score: Score | None = None


def _target(state, identifier):
    aliases = [(name, row) for name, row in state['profiles'].items() if row['environment_id'] == identifier]
    if not aliases: raise Ineligible('the environment has no profile aliases')
    name, row = min(aliases, key=lambda item: (
        item[1]['id'] != state.get('default_profile_id'), item[0].casefold(), item[0]))
    return Target(name, row['id'], identifier)


def choose(manager, *, model=None, args=(), environment_ids=None):
    state = manager.state()
    policy = selection_policy(manager)
    allowed = set(environment_ids) if environment_ids is not None else None
    def default(current):
        target = resolve(current)
        if allowed is not None and target.environment_id not in allowed:
            raise SelectionUnavailable('The default profile cannot continue the selected native session')
        return target
    conditional = policy.selection.strategy == 'comparable-or-default'
    if policy.selection.strategy == 'default' or conditional and manager.provider_spec.integrations.adapter == 'disabled':
        target = default(state)
        return Decision(target, revision(state, target.environment_id), 'Configured default profile', model)
    identifiers = {row['environment_id'] for row in state['profiles'].values()}
    if allowed is not None: identifiers &= allowed
    if not identifiers and allowed is not None:
        raise SelectionUnavailable('No profile can continue the selected native session')
    if not identifiers:
        raise Error(f'Create or connect a {manager.provider} profile before starting')
    observed = inspect_environments(manager, [_target(state, key) for key in sorted(identifiers)], model=model, args=args)
    excluded = []
    current = manager.state()
    def fallback(reason):
        target = default(current)
        snapshot = observed.get(target.environment_id)
        if snapshot:
            try: value = score(snapshot, policy, model=snapshot.get('selected_model'), allow_exhausted=True)
            except Ineligible: pass
            else:
                if not value.unlimited and value.remaining_percent == 0:
                    raise SelectionUnavailable(f'The default {manager.provider} profile is exhausted. Choose explicitly with agent {manager.provider} run PROFILE')
        return Decision(target, revision(current, target.environment_id), 'Configured default profile; ' + reason, model)
    if conditional:
        groups = {comparison_key(snapshot) for snapshot in observed.values()}
        if None in groups or len(groups) != 1: return fallback('model routes are not comparable')
    ranked = []
    for key in sorted(observed):
        name = str(_target(state, key))
        try:
            if observed[key] is None: raise Ineligible('budget inspection failed', 'unavailable')
            if key not in current['environments'] or revision(state, key) != revision(current, key):
                raise Ineligible('environment settings changed during selection')
            target = _target(current, key)
            selected_model = effective_model(manager, target, args, model) or observed[key].get('selected_model')
            value = score(observed[key], policy, model=selected_model)
            if not matches_observation(manager, target, observed[key]):
                raise Ineligible('budget reading changed during selection')
            ranked.append(Decision(target, revision(current, key), 'Highest remaining usable allowance',
                selected_model, observed[key], value))
        except Ineligible as error:
            failure = observed[key].get('error') if observed[key] else None
            excluded.append((name, failure['message'] if failure else str(error)))
    if not ranked:
        if conditional and not any(reason == 'an applicable quota is exhausted' for _, reason in excluded):
            return fallback('no qualified allowance is available')
        details = '; '.join(name + ': ' + reason for name, reason in sorted(excluded)[:8])
        raise SelectionUnavailable(f'No {manager.provider} profile has fresh usable allowance. {details}. '
                    f'Inspect agent {manager.provider} budget --all --refresh --json, '
                    f'or choose explicitly with agent {manager.provider} run PROFILE')
    return min(ranked, key=lambda decision: (
        -decision.score.key[0], -decision.score.key[1],
        decision.target.profile_id != current.get('default_profile_id'),
        str(decision.target).casefold(), str(decision.target)))


def verify(manager, decision):
    state = manager.state()
    target = decision.target
    if (target.environment_id not in state['environments']
            or revision(state, target.environment_id) != decision.environment_revision):
        raise Error('The selected environment changed. Retry start.')
    if decision.budget is not None:
        try:
            score(decision.budget, selection_policy(manager), model=decision.model)
            if not matches_observation(manager, target, decision.budget): raise Ineligible('changed')
        except (Ineligible, OSError):
            raise Error('The selected budget changed or expired. Retry start.') from None


def select_profile(manager, *, model=None, args=()):
    decision = choose(manager, model=model, args=args)
    result = inspect_profile(manager, decision.target)
    result['selection'] = dict(reason=decision.reason, model=decision.model,
        remaining_percent=decision.score.remaining_percent if decision.score else None,
        unlimited=decision.score.unlimited if decision.score else False)
    return result
