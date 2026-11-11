"""Select a paying native connection without summing unrelated integrations."""
from agent_manager.models.schema import selector
from agent_manager.providers.arguments import effective_model
from .readers import REASONS


def comparison_key(snapshot):
    """Reader-specific scope qualification stays out of shared ranking/UI code."""
    route = snapshot.get('selected_route') or {}
    model = snapshot.get('selected_model')
    if (snapshot.get('source') != 'opencode-integrations' or route.get('service_id') != 'openai'
            or route.get('budget_kind') != 'subscription' or route.get('auth_method') != 'oauth'
            or not isinstance(model, str) or not model): return None
    return ('openai', 'codex', model)


def qualified(snapshot):
    return comparison_key(snapshot) is not None and all(
        row['scope'].get('surface') == 'codex' and row['scope'].get('limit_id') == 'codex'
        and row['unit'] == 'percent' and row['kind'] == 'allowance' for row in snapshot['buckets'])


def get_budget(manager, profile=None, *, model=None, cwd=None, refresh=False, cached=False):
    target = manager.resolve_profile(profile)
    catalog = manager.integrations(target, cwd=cwd, refresh=refresh, cached=cached)
    selected = effective_model(manager, target, (), model) or catalog['default_model']
    return _snapshot(target, catalog, selected)


def _snapshot(target, catalog, selected):
    if selected is not None: selector(selected)
    routes = [row for row in catalog['routes'] if selected and selected.startswith(row['provider_id'] + '/')]
    route = max(routes, key=lambda row: len(row['provider_id'])) if routes else None
    integration = next((row for row in catalog['integrations'] if route and row['id'] == route['integration_id']), None)
    allowance = integration['budget'] if integration and route['reviewed'] else None
    connection = integration['active_connection'] if integration and route['reviewed'] else None
    status = allowance['status'] if allowance else 'unsupported' if route else 'unknown'
    reason = REASONS[allowance['reason']] if allowance else (
        'The selected model has no reviewed paying integration.' if selected else 'Select a model to inspect its paying integration.')
    if catalog['status'] in ('stale', 'unavailable', 'unsupported', 'unknown'):
        status = catalog['status']
    return dict(schema='agent-manager/budget-1', provider=catalog['provider'], environment_id=catalog['environment_id'],
        profiles=catalog['profiles'], scope='provider-connection', cwd=catalog['cwd'], selected_model=selected,
        selected_route=dict(provider_id=route['provider_id'], integration_id=route['integration_id'],
            service_id=route['service_id'], cli_providers=integration['cli_providers'] if allowance else [],
            budget_kind=integration['budget_kind'] if allowance else 'unknown',
            connection_id=connection['id'] if connection else None, auth_method=connection['method'] if connection else None) if route else None,
        source='opencode-integrations', status=status, reason=reason,
        observed_at=allowance['observed_at'] if allowance else None, checked_at=catalog['checked_at'],
        inspected_at=catalog['inspected_at'], cached=catalog['cached'], error=catalog['error'],
        buckets=allowance['buckets'] if allowance else [], integrations=catalog['integrations'],
        source_revision=catalog['source_revision'])


def matches_observation(manager, target, observation):
    """Validate the exact cached connection reading without probes or leases."""
    from pathlib import Path
    import time
    from . import service
    from agent_manager.models.schema import ModelError
    try:
        directory = Path(observation['cwd'])
        policy = service._policy(manager)
        _, revision, path = service.context(manager, target, directory, policy=policy)
        if revision != observation['source_revision']: return False
        now = time.time()
        value = service._read(path, revision, now)
        if value is None or value['failure_code'] is not None: return False
        catalog = service._result(manager, target, directory, revision, value, now, True)
        current = _snapshot(target, catalog, observation['selected_model'])
        return (current['status'] in ('ok', 'partial') and current['environment_id'] == observation['environment_id']
            and current['observed_at'] == observation['observed_at'] and current['checked_at'] == observation['checked_at']
            and current['selected_route'] == observation['selected_route'] and current['buckets'] == observation['buckets'])
    except (KeyError, TypeError, ValueError, ModelError, OSError): return False
