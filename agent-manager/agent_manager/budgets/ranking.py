"""Compare applicable account windows without inventing token or dollar capacity."""
from dataclasses import dataclass
from fnmatch import fnmatchcase
import time

from .models import BudgetError, number


class Ineligible(ValueError):
    """A fixed, public explanation for excluding an environment."""

    def __init__(self, reason, status='unknown'):
        self.status = status
        super().__init__(reason)


@dataclass(frozen=True)
class Score:
    remaining_percent: float | None
    unlimited: bool
    buckets: tuple[str, ...]
    expires_at: float

    @property
    def key(self):
        return (int(self.unlimited), self.remaining_percent or 0)


def _families(value, policy):
    if not value: return set()
    return {key for key, patterns in policy.model_families
            if any(fnmatchcase(value.casefold(), pattern.casefold()) for pattern in patterns)}


def applicable(row, policy, model):
    if row['kind'] != 'allowance' or not any(fnmatchcase(row['id'], pattern) for pattern in policy.buckets):
        return False
    requested = _families(model, policy)
    scoped = _families(row['scope'].get('model') or row['scope'].get('model_group'), policy)
    # Unknown model/scope labels remain constraints. Exclude only a proven other family.
    return not requested or not scoped or bool(requested & scoped)


def comparison_key(snapshot):
    """A connection percentage is comparable only along the same model route."""
    if snapshot is None or snapshot.get('scope') != 'provider-connection': return None
    from agent_manager.integrations.budgets import comparison_key as connection_key
    return connection_key(snapshot)


def score(snapshot, policy, *, model=None, now=None, allow_exhausted=False):
    now = time.time() if now is None else now
    if snapshot['scope'] == 'provider-connection':
        from agent_manager.integrations.budgets import qualified
        if comparison_key(snapshot) is None:
            raise Ineligible('complete comparable connection allowances are not available', 'unknown')
        if not qualified(snapshot):
            raise Ineligible('the connection returned unqualified allowance scopes', 'unknown')
        if model is not None and model != snapshot['selected_model']:
            raise Ineligible('the budget describes a different model route', 'unknown')
    if (snapshot['scope'] not in ('provider-account', 'provider-connection') or snapshot.get('error')
            or snapshot['status'] not in ('ok', 'partial', 'stale')):
        status = snapshot['status'] if snapshot['status'] in ('unknown', 'stale', 'unsupported') else 'unavailable'
        raise Ineligible('account allowance is unavailable', status)
    observed = snapshot.get('observed_at')
    try:
        number(observed)
        if not 0 <= now - observed < policy.cache_ttl: raise Ineligible('budget reading is stale', 'stale')
        rows = [row for row in snapshot['buckets'] if applicable(row, policy.selection, model)]
        if not rows: raise Ineligible('no applicable allowance windows')
        percentages, expires = [], [observed + policy.cache_ttl]
        for row in rows:
            reset = row['resets_at']
            if reset is not None:
                if reset <= now: raise Ineligible('quota reset has passed; refresh required', 'stale')
                expires.append(reset)
            if row['status'] in ('unknown', 'stale', 'disabled'):
                raise Ineligible('an applicable quota is unknown, stale or disabled', row['status'])
            if row['unlimited']: continue
            percent = row['remaining_percent']
            if percent is None:
                limit, remaining = row['limit'], row['remaining']
                if limit is None or remaining is None or number(limit) == 0:
                    raise Ineligible('an applicable quota has no comparable allowance')
                percent = min(100, 100 * number(remaining) / limit)
            percent = number(percent, maximum=100)
            if percent <= 0 and not allow_exhausted: raise Ineligible('an applicable quota is exhausted', 'exhausted')
            percentages.append(percent)
    except (BudgetError, KeyError, TypeError):
        raise Ineligible('account allowance is unrecognized') from None
    return Score(min(percentages) if percentages else None, not percentages,
                 tuple(row['id'] for row in rows), min(expires))
