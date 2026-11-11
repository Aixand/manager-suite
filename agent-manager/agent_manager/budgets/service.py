"""Environment-scoped observations, alias sharing, freshness and explicit refresh."""
from dataclasses import asdict, replace
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import shutil
import time

from agent_manager.errors import Error
from agent_manager.profiles.identities import for_environment
from agent_manager.storage.files import atomic
from agent_manager.storage.locks import locks, shared_environment_keys
from . import adapters
from .models import BudgetError, SCHEMA, observation_status, timestamp, validate_buckets

MAX_CACHE = 256 * 1024


def selection_policy(manager):
    policy = manager.provider_spec.budget
    integration = manager.provider_spec.integrations
    return replace(policy, cache_ttl=integration.cache_ttl) if integration.adapter != 'disabled' else policy


def invalidate(manager, environment_id):
    """Discard disposable observations before native login changes or retirement."""
    (manager.root / 'budgets' / (environment_id + '.json')).unlink(missing_ok=True)


def _revision(manager, target, home):
    executable = shutil.which(manager.config['executable'])
    program = None
    if executable:
        path = Path(executable).resolve()
        try:
            info = path.stat()
            program = [str(path), info.st_mtime_ns, info.st_size]
        except OSError: pass
    value = dict(home=str(home), policy=asdict(manager.provider_spec.budget), program=program,
        login=manager.state().get('login_status', {}).get(target.environment_id))
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _cached(path, revision, now):
    try:
        if path.is_symlink() or path.stat().st_size > MAX_CACHE: return None
        value = json.loads(path.read_text())
        if (set(value) != {'revision', 'observed_at', 'checked_at', 'failure_code', 'buckets'} or value['revision'] != revision
                or timestamp(value['observed_at']) is None or value['observed_at'] > now
                or timestamp(value['checked_at']) is None or not value['observed_at'] <= value['checked_at'] <= now
                or value['failure_code'] not in (None, 'timeout', 'unavailable')):
            return None
        value['buckets'] = validate_buckets(value['buckets'])
        return value
    except (OSError, ValueError, TypeError, KeyError, BudgetError):
        return None


def _result(manager, target, observation, *, now, cached, status=None, error=None):
    policy = manager.provider_spec.budget
    rows = validate_buckets(observation['buckets']) if observation else []
    observed = observation['observed_at'] if observation else None
    if error is None and observation and observation.get('failure_code'):
        error = BudgetError(observation['failure_code'])
    expired = observed is not None and now - observed >= policy.cache_ttl
    for row in rows:
        if expired or error or row['resets_at'] is not None and row['resets_at'] <= now:
            row['status'] = 'stale'
    if status is None:
        status = 'stale' if error or any(row['status'] == 'stale' for row in rows) or expired else observation_status(rows)
    state = manager.state()
    names = sorted(name for name, row in state['profiles'].items() if row['environment_id'] == target.environment_id)
    return dict(schema=SCHEMA, provider=manager.provider, environment_id=target.environment_id,
        profiles=names, scope='provider-account', status=status, observed_at=observed,
        checked_at=observation['checked_at'] if observation else None, inspected_at=now,
        cached=cached, source=policy.adapter, buckets=rows,
        reason=policy.reason or 'No account-budget reader is declared.' if policy.adapter == 'disabled' else None,
        error=dict(code=error.code, message=str(error)) if error else None)


def get_budget(manager, profile=None, *, refresh=False, cached=False, cwd=None, model=None):
    """Read one environment. Native login stays provider-owned; no model turn is submitted."""
    if type(refresh) is not bool or type(cached) is not bool or refresh and cached:
        raise Error('Choose either a native refresh or cached-only budget inspection')
    target = manager.resolve_profile(profile)
    if manager.provider_spec.integrations.adapter != 'disabled':
        from agent_manager.integrations.budgets import get_budget as integration_budget
        return integration_budget(manager, target, refresh=refresh, cached=cached, cwd=cwd, model=model)
    policy = manager.provider_spec.budget
    if policy.adapter == 'disabled':
        return _result(manager, target, None, now=time.time(), cached=False, status='unsupported')
    path = manager.root / 'budgets' / (target.environment_id + '.json')
    # Native login/logout and retirement use the same environment lease. Renaming
    # aliases takes no environment lease and cannot redirect this captured target.
    with locks(manager.lock_root, 'budget-' + target.environment_id,
               'environment-' + target.environment_id, wait=True,
               shared_keys=shared_environment_keys(manager, target.environment_id)):
        home = manager.home(target)
        now = time.time()
        revision = _revision(manager, target, home)
        previous = _cached(path, revision, now)
        reusable = (previous is not None and now - previous['checked_at'] < policy.cache_ttl
            and (previous['failure_code'] is not None or now - previous['observed_at'] < policy.cache_ttl
                 and not any(row['resets_at'] is not None and row['resets_at'] <= now for row in previous['buckets'])))
        if cached or reusable and not refresh:
            return _result(manager, target, previous, now=now, cached=True)
        try:
            rows = validate_buckets(adapters.read(manager, home, policy))
            observed = time.time()
            observation = dict(revision=revision, observed_at=observed, checked_at=observed,
                failure_code=None, buckets=rows)
        except BudgetError as error:
            # Only temporary transport failures may retain an explicitly stale
            # observation. Sign-in, version and format failures discard it.
            if previous is not None and error.code in ('timeout', 'unavailable'):
                previous.update(checked_at=time.time(), failure_code=error.code)
                atomic(path, (json.dumps(previous, allow_nan=False) + '\n').encode())
                result = _result(manager, target, previous, now=time.time(), cached=True,
                    status='stale', error=error)
                for row in result['buckets']: row['status'] = 'stale'
                return result
            path.unlink(missing_ok=True)
            result = _result(manager, target, None, now=time.time(), cached=False,
                status='unsupported' if error.code == 'unsupported' else 'unavailable', error=error)
            result['checked_at'] = result['inspected_at']
            return result
        if _revision(manager, target, home) != revision:
            raise Error('The environment changed during budget inspection. Refresh again.')
        atomic(path, (json.dumps(observation, allow_nan=False) + '\n').encode())
        return _result(manager, target, observation, now=time.time(), cached=False)


def list_budgets(manager, *, refresh=False, cached=False, cwd=None, model=None):
    """One observation per environment, including unnamed ones; never sum accounts."""
    if type(refresh) is not bool or type(cached) is not bool or refresh and cached:
        raise Error('Choose either a native refresh or cached-only budget inspection')
    state = manager.state()
    return [get_budget(manager, for_environment(state, key), refresh=refresh, cached=cached, cwd=cwd, model=model)
            for key in sorted(state['environments'])]


def inspect_environments(manager, targets, *, model=None, args=()):
    """Bound interactive reads and share one observation across captured aliases.

    None denotes an inspection failure; raw native or filesystem errors never
    become picker labels. Normal cache freshness rules apply to every read.
    """
    unique = {target.environment_id: target for target in targets}
    if not unique: return {}
    observations = {}
    def inspect(target):
        if manager.provider_spec.integrations.adapter != 'disabled':
            from agent_manager.providers.arguments import effective_model
            return manager.budget(target, model=effective_model(manager, target, args, model))
        return manager.budget(target)
    from contextvars import copy_context
    context = copy_context()
    with ThreadPoolExecutor(max_workers=min(4, len(unique))) as workers:
        pending = {workers.submit(context.copy().run, inspect, target): key for key, target in unique.items()}
        for future in as_completed(pending):
            key = pending[future]
            try:
                observations[key] = future.result()
            except (Error, OSError):
                observations[key] = None
    return observations


def matches_observation(manager, target, observation):
    """Read-only launch validation, safe while environment/registry leases are held.

    Never starts a provider or acquires another lease. A concurrent refresh, login
    change or executable replacement invalidates an accepted selection.
    """
    try:
        if observation.get('scope') == 'provider-connection':
            from agent_manager.integrations.budgets import matches_observation as matches_integration
            return matches_integration(manager, target, observation)
        now = time.time()
        home = manager.home(target)
        revision = _revision(manager, target, home)
        path = manager.root / 'budgets' / (target.environment_id + '.json')
        current = _cached(path, revision, now)
        return bool(current is not None and current['failure_code'] is None
            and current['observed_at'] == observation['observed_at']
            and current['checked_at'] == observation['checked_at']
            and current['buckets'] == validate_buckets(observation['buckets']))
    except (Error, OSError, BudgetError):
        return False


def display(result):
    """Small CLI presentation; JSON is the complete machine-readable contract."""
    rows = result if isinstance(result, list) else [result]
    for snapshot in rows:
        names = ', '.join(snapshot['profiles']) or snapshot['environment_id']
        print(f"{names}: {snapshot['status']} ({snapshot['source']})")
        if snapshot.get('scope') == 'provider-connection':
            print('  Model: ' + (snapshot['selected_model'] or 'unknown'))
            route = snapshot.get('selected_route')
            if route: print('  Paying integration: ' + str(route['integration_id']))
            if not snapshot['buckets']:
                from agent_manager.integrations.readers import REASONS
                for integration in snapshot.get('integrations', []):
                    allowance = integration['budget']
                    print(f"  {integration['id']}: {allowance['status']} — {REASONS[allowance['reason']]}")
        for row in snapshot['buckets']:
            if row['unlimited']:
                value = 'unlimited'
            elif row['remaining_percent'] is not None:
                value = f"{row['remaining_percent']:g}% remaining"
            elif row['remaining'] is not None:
                value = f"{row['remaining']:g} {row['unit']} remaining"
            else:
                value = 'unknown'
            from datetime import datetime, timezone
            reset = ('; resets ' + datetime.fromtimestamp(row['resets_at'], timezone.utc).isoformat()
                     if row['resets_at'] is not None else '')
            print(f"  {row['label']}: {value} [{row['status']}]{reset}")
        if snapshot['error']: print('  ' + snapshot['error']['message'])
        elif snapshot['reason']: print('  ' + snapshot['reason'])
