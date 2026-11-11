"""Public integration metadata. Credential values and provider settings never pass."""
from agent_manager.budgets.models import BudgetError, validate_buckets
from agent_manager.models.schema import ModelError, number, selector, text
from . import readers

SCHEMA = 'agent-manager/integrations-2'
MAX_ITEMS = 1024
METHODS = ('key', 'oauth', 'external', 'env', 'command')


class IntegrationError(ModelError):
    MESSAGES = {**ModelError.MESSAGES,
        'timeout': 'Native integration inspection timed out. Refresh to retry.',
        'unavailable': 'Native integration metadata is unavailable. Refresh to retry.',
        'unsupported': 'This CLI version has no qualified native integration adapter.',
        'rpc-invalid-input': 'OpenCode rejected the budget probe RPC input schema.',
        'rpc-method-not-found': 'The native budget probe RPC has no read method.',
        'rpc-rejected': 'OpenCode rejected the budget probe request. The native check records the HTTP diagnostic.',
        'invalid-response': 'The provider returned unrecognized integration metadata.'}


def _rows(value):
    if not isinstance(value, list) or len(value) > MAX_ITEMS: raise ModelError('invalid-response')
    return value


def normalize(native, bridge, policy, now):
    rules = {row.id: row for row in policy.rules}
    raw_integrations = _rows(native.get('data'))
    details = {}
    if bridge is not None:
        for row in _rows(bridge.get('integrations')):
            key = selector(row['id'])
            if key in details: raise ModelError('invalid-response')
            details[key] = row
    routes = []
    seen = set()
    for row in _rows(bridge.get('providers') if bridge else []):
        key = selector(row['id'])
        if key in seen: raise ModelError('invalid-response')
        seen.add(key)
        integration = selector(row['integration_id']) if row.get('integration_id') is not None else None
        package = text(row['package'])
        rule = rules.get(integration)
        if type(row.get('endpoint_reviewed')) is not bool: raise ModelError('invalid-response')
        reviewed = bool(rule and package in rule.packages and row['endpoint_reviewed'])
        routes.append(dict(provider_id=key, integration_id=integration, package=package,
            service_id=rule.service_id if reviewed else None, reviewed=reviewed))
    result, seen = [], set()
    for row in raw_integrations:
        key = selector(row['id'])
        if key in seen: raise ModelError('invalid-response')
        seen.add(key)
        rule = rules.get(key)
        detail = details.get(key)
        active = readers.connection(detail.get('active_connection')) if detail else None
        methods = []
        for method in _rows(row.get('methods')):
            kind = method.get('type')
            if kind not in METHODS: raise ModelError('invalid-response')
            methods.append(dict(id=selector(method['id']) if method.get('id') else None, type=kind))
        connections = []
        connection_ids = set()
        for item in _rows(row.get('connections')):
            clean = readers.connection(item)
            if clean is None or clean['id'] in connection_ids: raise ModelError('invalid-response')
            connection_ids.add(clean['id'])
            native_status = item.get('status')
            clean.update(active=clean['id'] == active['id'] if detail and active else False if detail else None,
                status='needs-auth' if isinstance(native_status, dict) and native_status.get('status') == 'needs_auth' else 'unknown')
            connections.append(clean)
        if active is not None and active['id'] not in connection_ids:
            # Public inventory and active selection must describe one snapshot.
            raise ModelError('invalid-response')
        authentication = rule.authentication_for(active['method']) if rule and active else None
        adapter = authentication.budget_adapter if authentication else 'disabled'
        reviewed = any(route['reviewed'] and route['integration_id'] == key for route in routes)
        if not rule: allowance = readers.empty_budget(adapter, 'unreviewed-integration')
        elif detail is None: allowance = readers.empty_budget(adapter, 'bridge-unavailable')
        elif not reviewed:
            allowance = readers.empty_budget(adapter, 'unreviewed-route')
        elif active is None: allowance = readers.empty_budget(adapter, 'no-active-connection')
        elif authentication is None:
            allowance = readers.empty_budget(adapter, 'unreviewed-method')
        elif adapter == 'disabled': allowance = readers.empty_budget(adapter, 'reader-unavailable')
        else:
            allowance = readers.budget(adapter, detail.get('budget'), observed_at=now)
        result.append(dict(id=key, name=text(row['name']), recognized=rule is not None, reviewed=reviewed,
            service_id=rule.service_id if rule else None, cli_providers=list(rule.cli_providers) if rule else [],
            budget_kind=authentication.budget_kind if authentication else 'unknown',
            connection_status='selected' if active else 'none' if detail else 'unknown',
            methods=methods, connections=connections, active_connection=active, budget=allowance))
    if details.keys() - seen: raise ModelError('invalid-response')
    for route in routes:
        if route['integration_id'] not in seen:
            route.update(reviewed=False, service_id=None)
    selected = bridge.get('default_model') if bridge else None
    return dict(integrations=sorted(result, key=lambda item: item['id']),
        routes=sorted(routes, key=lambda item: item['provider_id']),
        default_model=selector(selected) if selected is not None else None,
        bridge_available=bridge is not None)


def validate_data(data):
    """Cache reads reject extra fields instead of reflecting edited raw native data."""
    def keys(row, expected):
        if not isinstance(row, dict) or set(row) != set(expected.split()): raise ModelError('invalid-response')
    def flag(value, nullable=False):
        if not (nullable and value is None) and type(value) is not bool: raise ModelError('invalid-response')
    keys(data, 'integrations routes default_model bridge_available')
    flag(data['bridge_available'])
    if data['default_model'] is not None: selector(data['default_model'])
    seen = set()
    for row in _rows(data['integrations']):
        keys(row, 'id name recognized reviewed service_id cli_providers budget_kind connection_status methods connections active_connection budget')
        key = selector(row['id']); text(row['name']); flag(row['reviewed']); flag(row['recognized'])
        if row['service_id'] is not None: selector(row['service_id'])
        for provider in _rows(row['cli_providers']): selector(provider)
        if row['budget_kind'] not in ('subscription', 'api-billing', 'credits', 'unknown'): raise ModelError('invalid-response')
        if row['connection_status'] not in ('selected', 'none', 'unknown'): raise ModelError('invalid-response')
        if key in seen: raise ModelError('invalid-response')
        seen.add(key)
        for method in _rows(row['methods']):
            keys(method, 'id type')
            if method['id'] is not None: selector(method['id'])
            if method['type'] not in METHODS: raise ModelError('invalid-response')
        for item in _rows(row['connections']):
            keys(item, 'id type method active status'); readers.connection(item); flag(item['active'], nullable=True)
            if item['status'] not in ('unknown', 'needs-auth'): raise ModelError('invalid-response')
        if row['active_connection'] is not None:
            keys(row['active_connection'], 'id type method'); readers.connection(row['active_connection'])
        allowance = row['budget']
        keys(allowance, 'adapter status reason observed_at buckets')
        if allowance['adapter'] not in ('disabled', 'openai-native', 'openrouter-key'): raise ModelError('invalid-response')
        if allowance['status'] not in ('ok', 'unknown', 'unsupported', 'unavailable', 'partial'): raise ModelError('invalid-response')
        if allowance['reason'] not in readers.REASONS: raise ModelError('invalid-response')
        number(allowance['observed_at'])
        try: allowance['buckets'] = validate_buckets(allowance['buckets'])
        except BudgetError: raise ModelError('invalid-response') from None
        if allowance['buckets'] and allowance['observed_at'] is None: raise ModelError('invalid-response')
    seen = set()
    for row in _rows(data['routes']):
        keys(row, 'provider_id integration_id package service_id reviewed')
        key = selector(row['provider_id']); text(row['package']); flag(row['reviewed'])
        if key in seen: raise ModelError('invalid-response')
        seen.add(key)
        if row['integration_id'] is not None: selector(row['integration_id'])
        if row['service_id'] is not None: selector(row['service_id'])
    return data
