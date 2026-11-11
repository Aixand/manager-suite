"""Convert public OpenCode-side observations to shared quota buckets."""
from agent_manager.budgets.models import BudgetError, bucket, number, validate_buckets
from agent_manager.models.schema import ModelError, selector, text

REASONS = {
    'no-active-connection': 'No active native connection is available.',
    'chatgpt-codex-allowance': 'Codex quota for the selected OpenCode ChatGPT connection.',
    'chatgpt-no-allowance': 'The selected ChatGPT connection returned no percentage allowance.',
    'missing-cli': 'OpenCode ChatGPT quota inspection requires the configured Codex executable.',
    'unsupported-codex-version': 'The Codex executable is outside the qualified quota-reader versions.',
    'api-billing-reader-unavailable': 'OpenAI API-key billing is separate; no reviewed billing observation is available.',
    'account-balance-unavailable': 'Key spending limits are reported; account-wide credit balance is unknown.',
    'unreviewed-method': 'This native authentication method has not been reviewed.',
    'unreviewed-integration': 'This integration has no reviewed Manager budget implementation.',
    'unreviewed-route': 'This runtime or endpoint has not been reviewed for this integration.',
    'bridge-unavailable': 'The native budget metadata plugin is unavailable.',
    'reader-unavailable': 'The service and connection are recognized; no budget reader is configured for this authentication type.',
    'connection-changed': 'The active native connection changed during inspection. Refresh again.',
    'authentication': 'Check sign-in in the selected OpenCode environment.',
    'timeout': 'The native budget query timed out. Refresh to retry.',
    'unavailable': 'The upstream budget observation is unavailable.',
    'invalid-response': 'The upstream budget response is unrecognized.',
}


def connection(value):
    if value is None: return None
    if not isinstance(value, dict) or value.get('type') not in ('credential', 'env'):
        raise ModelError('invalid-response')
    method = 'key' if value['type'] == 'env' else value.get('method')
    if method not in ('key', 'oauth', 'external'): raise ModelError('invalid-response')
    key = value.get('id') if value['type'] == 'credential' else value.get('id') or 'env:' + selector(value.get('name'))
    return dict(id=selector(key), type=value['type'], method=method)


def budget(adapter, value, *, observed_at):
    if not isinstance(value, dict): raise ModelError('invalid-response')
    status, reason = value.get('status'), value.get('reason')
    if status not in ('ok', 'unknown', 'unsupported', 'unavailable', 'partial') or reason not in REASONS:
        raise ModelError('invalid-response')
    rows = []
    observation = value.get('observation')
    if observation is not None:
        if adapter == 'openai-native':
            return openai_budget(observation, status, reason, observed_at)
        if adapter != 'openrouter-key' or status != 'partial' or not isinstance(observation, dict):
            raise ModelError('invalid-response')
        try:
            def nullable(key):
                return number(observation[key]) if observation.get(key) is not None else None
            limit, remaining, used = (nullable(key) for key in ('limit', 'remaining', 'used'))
            rows.append(bucket('openrouter:key', 'OpenRouter key spending allowance', unit='usd',
                limit=limit, remaining=remaining, used=used,
                window_seconds={'daily': 86400, 'weekly': 604800}.get(observation.get('reset_interval'))))
            # A missing key cap cannot establish unlimited account funds. Even a
            # positive key allowance does not establish a positive account balance.
            rows.append(bucket('openrouter:account-balance', 'OpenRouter account credit balance', unit='usd'))
            shared = observation.get('budgets', [])
            if not isinstance(shared, list) or len(shared) > 64: raise ModelError('invalid-response')
            for item in shared:
                scope = item.get('scope')
                if scope not in ('member', 'workspace', 'organization', 'key', 'shared'): raise ModelError('invalid-response')
                rows.append(bucket('openrouter:' + selector(item['id']), 'OpenRouter ' + scope + ' spending allowance',
                    unit='usd', limit=item.get('limit'), remaining=item.get('remaining'), used=item.get('used'),
                    resets_at=item.get('resets_at')))
            rows = validate_buckets(rows)
        except (BudgetError, KeyError, TypeError): raise ModelError('invalid-response') from None
    elif status in ('ok', 'partial'): raise ModelError('invalid-response')
    return dict(adapter=adapter, status=status, reason=reason, observed_at=observed_at if observation is not None else None, buckets=rows)


def openai_budget(observation, status, reason, observed_at):
    """Only public quota windows reach Python; credentials stay in native code."""
    if (not isinstance(observation, dict) or set(observation) != {'windows'}
            or reason not in ('chatgpt-codex-allowance', 'chatgpt-no-allowance')):
        raise ModelError('invalid-response')
    windows = observation['windows']
    if not isinstance(windows, list) or len(windows) > 128: raise ModelError('invalid-response')
    rows = []
    try:
        for row in windows:
            if not isinstance(row, dict) or set(row) != {
                    'limit_id', 'window', 'used_percent', 'remaining_percent', 'window_minutes', 'resets_at'}:
                raise ModelError('invalid-response')
            limit_id = selector(row['limit_id'])
            if row['window'] not in ('primary', 'secondary'): raise ModelError('invalid-response')
            used = number(row['used_percent'], maximum=100) if row['used_percent'] is not None else None
            remaining = number(row['remaining_percent'], maximum=100) if row['remaining_percent'] is not None else None
            if (used is None) != (remaining is None) or used is not None and abs(used + remaining - 100) > 1e-6:
                raise ModelError('invalid-response')
            minutes = row['window_minutes']
            rows.append(bucket('openai:' + limit_id + ':' + row['window'],
                'OpenAI ChatGPT · ' + limit_id + ' · ' + row['window'], used=used, remaining=remaining,
                remaining_percent=remaining, limit=100, resets_at=row['resets_at'],
                window_seconds=number(minutes) * 60 if minutes is not None else None,
                scope={'surface': 'codex', 'limit_id': limit_id}))
        rows = validate_buckets(rows)
    except (BudgetError, KeyError, TypeError): raise ModelError('invalid-response') from None
    from agent_manager.budgets.models import observation_status
    if status != observation_status(rows): raise ModelError('invalid-response')
    return dict(adapter='openai-native', status=status, reason=reason, observed_at=observed_at, buckets=rows)


def empty_budget(adapter, reason):
    return dict(adapter=adapter, status='unsupported' if reason.startswith('unreviewed') else 'unknown',
        reason=reason, observed_at=None, buckets=[])
