"""Manual execution choices, ordered by the shared account-allowance rules."""
from dataclasses import dataclass

from agent_manager.budgets.ranking import Ineligible, comparison_key, score
from agent_manager.budgets.service import inspect_environments, matches_observation, selection_policy
from agent_manager.environments.operations import revision
from agent_manager.errors import Error
from agent_manager.profiles.identities import Target, resolve
from agent_manager.providers.arguments import effective_model


HEADERS = ['Profile', 'Budget', 'Native home']
LABELS = dict(unknown='Unknown', stale='Stale', disabled='Disabled',
              unavailable='Unavailable', unsupported='Unsupported CLI')


@dataclass(frozen=True)
class Choice:
    target: Target
    home: str
    environment_revision: str
    budget: str
    order: tuple
    comparable: bool = True

    @property
    def row(self): return (str(self.target), self.budget, self.home)


def choices(manager, profiles, *, model=None, args=()):
    state = manager.state()
    targets = [resolve(state, profile) for profile in profiles]
    policy = selection_policy(manager)
    enabled = policy.adapter != 'disabled' or manager.provider_spec.integrations.adapter != 'disabled'
    observed = inspect_environments(manager, targets, model=model, args=args) if enabled else {}
    grouped = {comparison_key(snapshot) for snapshot in observed.values()}
    comparable = (manager.provider_spec.integrations.adapter == 'disabled'
        or bool(grouped) and None not in grouped and len(grouped) == 1)
    current = manager.state()
    aliases = {row['id']: name for name, row in current['profiles'].items()}
    result = []
    for original in targets:
        # Do not display a deleted alias or let its reused name enter this table.
        if original.profile_id not in aliases: continue
        target = resolve(current, aliases[original.profile_id])
        if target.environment_id != original.environment_id:
            raise Error('Execution profiles changed. Retry continue.')
        label, order = 'N/A', (1, 0, 0)
        if enabled:
            try:
                snapshot = observed[target.environment_id]
                if snapshot is None: raise Ineligible('budget inspection failed', 'unavailable')
                value = score(snapshot, policy,
                    model=effective_model(manager, target, args, model), allow_exhausted=True)
                if not matches_observation(manager, target, snapshot):
                    raise Ineligible('budget reading changed', 'stale')
                label = ('Unlimited' if value.unlimited else
                         '0% — exhausted' if value.remaining_percent == 0 else f'{value.remaining_percent:g}%')
                if snapshot['scope'] == 'provider-connection':
                    route = snapshot['selected_route']
                    paying = next((row['name'] for row in snapshot['integrations']
                        if row['id'] == route['integration_id']), route['service_id'])
                    label += ' · ' + paying
                order = (0, -value.key[0], -value.key[1])
            except Ineligible as error:
                label = LABELS[error.status]
        if not comparable: order = (1, 0, 0)
        result.append(Choice(target, str(manager.home(target)), revision(current, target.environment_id), label,
            (*order, target.profile_id != current.get('default_profile_id'), str(target).casefold(), str(target)), comparable))
    return sorted(result, key=lambda item: item.order)


def verify(manager, choice):
    """Budget is advisory; only captured environment settings guard this launch."""
    state = manager.state()
    if (choice.target.environment_id not in state['environments']
            or revision(state, choice.target.environment_id) != choice.environment_revision):
        raise Error('The selected environment settings changed. Retry continue.')


def pick_profile(manager, profiles, *, picker, title, detail, model=None, args=()):
    available = choices(manager, profiles, model=model, args=args)
    if not available: raise Error('Execution profiles changed. Retry continue.')
    selected = picker(title, HEADERS, [item.row for item in available],
        detail + ' | Budget snapshot; all rows selectable'
        + (' | Mixed or unknown model routes: default/name order' if not all(item.comparable for item in available) else ''))
    if selected is None: return None
    choice = available[selected]
    verify(manager, choice)
    return choice
