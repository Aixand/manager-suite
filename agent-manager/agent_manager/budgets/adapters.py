"""Provider wire formats terminate here; the service sees only normalized buckets."""
import hashlib
import json
import re

from agent_manager._version import __version__
from .models import BudgetError, bucket, failure, identifier, number, text
from .transport import deadline, rpc, run

# Manifests select a built-in reader, never Python imports or credential paths.
PROVIDERS = {'codex-rate-limits': 'codex', 'claude-usage': 'claude',
             'copilot-quota': 'copilot', 'antigravity-usage': 'antigravity'}


def parse_codex(value):
    groups = value.get('rateLimitsByLimitId')
    if groups is None:
        current = value.get('rateLimits')
        groups = {current.get('limitId') or 'default': current} if isinstance(current, dict) else {}
    if not isinstance(groups, dict): raise BudgetError('invalid-response')
    rows = []
    for key, group in groups.items():
        key = identifier(key)
        if not isinstance(group, dict): raise BudgetError('invalid-response')
        scope = {'limit_id': key}
        for window in ('primary', 'secondary'):
            value = group.get(window)
            if value is None: continue
            if not isinstance(value, dict): raise BudgetError('invalid-response')
            used = value.get('usedPercent')
            used = number(used, maximum=100) if used is not None else None
            duration = value.get('windowDurationMins')
            rows.append(bucket(key + ':' + window, key + ' · ' + window,
                used=used, limit=100, remaining=None if used is None else 100 - used,
                remaining_percent=None if used is None else 100 - used,
                resets_at=value.get('resetsAt'),
                window_seconds=None if duration is None else number(duration) * 60, scope=scope))
        credits = group.get('credits')
        if credits is not None:
            if not isinstance(credits, dict): raise BudgetError('invalid-response')
            # The balance can be decimal text. No dollar conversion is inferred.
            balance = credits.get('balance')
            if isinstance(balance, str):
                try: balance = float(balance)
                except ValueError: raise BudgetError('invalid-response') from None
            rows.append(bucket(key + ':credits', key + ' · credits', unit='credits',
                remaining=balance, unlimited=credits.get('unlimited', False), scope=scope))
    return rows


def parse_claude(output, code=0):
    try:
        messages = [json.loads(line) for line in output.splitlines() if line.strip()]
        if any(not isinstance(row, dict) for row in messages): raise ValueError
    except (ValueError, TypeError, UnicodeError): raise BudgetError('invalid-response') from None
    finals = [row for row in messages if row.get('type') == 'result']
    if code or len(finals) != 1 or finals[0].get('is_error'):
        raise failure(finals[0] if finals else {})
    final = finals[0]
    if (final.get('local_command') != 'usage' or final.get('num_turns') != 0
            or final.get('duration_api_ms') != 0 or final.get('total_cost_usd') != 0):
        raise BudgetError('invalid-response')
    reports = [row['usage_report'] for row in messages if 'usage_report' in row
               and isinstance(row.get('local_command_run'), dict)
               and row['local_command_run'].get('command') == 'usage']
    if len(reports) != 1 or not isinstance(reports[0], dict): raise BudgetError('unsupported')
    rates = reports[0].get('rate_limits')
    if rates is None: return []
    if not isinstance(rates, dict) or rates.get('limits') is not None and not isinstance(rates['limits'], list):
        raise BudgetError('invalid-response')
    rows = []
    # The CLI deliberately returns null rows when it has no current server reply;
    # its cached screen bars and header estimates are not an account observation.
    for meter in rates.get('limits') or []:
        if not isinstance(meter, dict): raise BudgetError('invalid-response')
        kind = identifier(meter.get('kind'))
        scope = meter.get('scope') or {}
        if not isinstance(scope, dict): raise BudgetError('invalid-response')
        scope = {key: text(value['display_name']) for key, value in scope.items()
                 if key in ('model', 'surface') and isinstance(value, dict) and 'display_name' in value}
        suffix = ':' + hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()[:16] if scope else ''
        used = meter.get('percent')
        used = number(used, maximum=100) if used is not None else None
        duration = {'session': 5 * 3600, 'weekly_all': 7 * 86400, 'weekly_scoped': 7 * 86400}.get(kind)
        rows.append(bucket('claude:' + kind + suffix, kind, used=used, limit=100,
            remaining=None if used is None else 100 - used,
            remaining_percent=None if used is None else 100 - used,
            resets_at=meter.get('resets_at'), window_seconds=duration, scope=scope))
    extra = rates.get('extra_usage')
    if extra is not None:
        if not isinstance(extra, dict) or type(extra.get('is_enabled')) is not bool:
            raise BudgetError('invalid-response')
        used, limit, percent = extra.get('used_credits'), extra.get('monthly_limit'), extra.get('utilization')
        used = None if used is None else number(used)
        limit = None if limit is None else number(limit)
        percent = None if percent is None else max(0, 100 - number(percent))
        dollars = extra.get('currency') == 'USD'
        # Native amounts are currency minor units; only USD is converted to dollars.
        rows.append(bucket('claude:extra-usage', 'Extra usage · monthly spending cap',
            kind='spending-cap', unit='usd' if dollars else 'percent',
            used=None if not dollars or used is None else used / 100,
            limit=None if not dollars or limit is None else limit / 100,
            remaining=max(0, limit - used) / 100 if dollars and used is not None and limit is not None else None,
            remaining_percent=percent, disabled=not extra['is_enabled']))
    return rows


def parse_antigravity(output, code=0):
    try:
        value = json.loads(output)
        if not isinstance(value, dict): raise ValueError
    except (ValueError, TypeError, UnicodeError): raise BudgetError('invalid-response') from None
    if code or value.get('status') != 'SUCCESS': raise failure(value.get('error', {}))
    usage = value.get('usage', {})
    if (not isinstance(usage, dict) or value.get('num_turns') != 0 or any(
            usage.get(key, 0) != 0 for key in ('input_tokens', 'output_tokens', 'thinking_tokens', 'total_tokens'))):
        raise BudgetError('invalid-response')
    command = value.get('command')
    if (not isinstance(command, dict) or command.get('name') not in ('usage', 'quota')
            or not isinstance(command.get('data'), dict)):
        raise BudgetError('unsupported')
    groups = command['data'].get('groups')
    if not isinstance(groups, list): raise BudgetError('invalid-response')
    rows = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get('buckets'), list):
            raise BudgetError('invalid-response')
        label = text(group.get('name'))
        for item in group['buckets']:
            if not isinstance(item, dict): raise BudgetError('invalid-response')
            window = identifier(item.get('window'))
            fraction = item.get('remaining_fraction')
            percent = None if fraction is None else 100 * number(fraction, maximum=1)
            amount = item.get('remaining_amount')
            # An amount without a declared unit cannot become a credit or dollar balance.
            unit = item.get('unit') if amount is not None else 'percent'
            known_amount = amount if unit in ('requests', 'credits', 'usd') else None
            rows.append(bucket(identifier(item.get('id')), label + ' · ' + window,
                remaining=known_amount if known_amount is not None else percent,
                unit=unit if known_amount is not None else 'percent', remaining_percent=percent,
                limit=100 if known_amount is None and percent is not None else None,
                disabled=item.get('disabled', False), resets_at=item.get('reset_time'),
                window_seconds={'5h': 5 * 3600, 'weekly': 7 * 86400}.get(window), scope={'model_group': label}))
    return rows


def parse_copilot(value):
    groups = value.get('quotaSnapshots')
    if groups is None: return []
    if not isinstance(groups, dict): raise BudgetError('invalid-response')
    rows = []
    for key, item in groups.items():
        key = identifier(key)
        if not isinstance(item, dict): raise BudgetError('invalid-response')
        limit, used = item.get('entitlementRequests'), item.get('usedRequests')
        unlimited = type(limit) in (int, float) and limit == -1
        limit = None if unlimited or limit is None else number(limit)
        used = None if used is None else number(used)
        percent = item.get('remainingPercentage')
        rows.append(bucket(key, key, unit='requests', limit=limit, used=used,
            remaining=max(0, limit - used) if limit is not None and used is not None else None,
            remaining_percent=percent, unlimited=unlimited, resets_at=item.get('resetDate')))
    return rows


def read(manager, home, policy):
    end = deadline(policy.timeout)
    arguments = ['--binary-version'] if manager.provider == 'copilot' else ['--version']
    output, code = run(manager, home, arguments, end)
    match = re.search(rb'(?:^|\s)(\d+\.\d+\.\d+)(?:\s|$)', output)
    if code or not match:
        raise BudgetError('unsupported')
    if match[1].decode() not in policy.versions:
        raise BudgetError('unsupported', installed_version=match[1].decode(), supported_versions=policy.versions)
    if policy.adapter == 'codex-rate-limits':
        with rpc(manager, home, ['app-server', '--listen', 'stdio://'], end) as request:
            request('initialize', {'clientInfo': {'name': 'agent_manager_budget', 'version': __version__}})
            request('initialized', notification=True)
            return parse_codex(request('account/rateLimits/read'))
    if policy.adapter == 'copilot-quota':
        arguments = ['--headless', '--stdio', '--no-auto-update',
            '--disable-client-telemetry', '--disable-builtin-mcps', '--no-custom-instructions', '--sync-session=off']
        with rpc(manager, home, arguments, end, framed=True) as request:
            status = request('status.get')
            if status.get('version') not in policy.versions or status.get('protocolVersion') != 3:
                raise BudgetError('unsupported')
            return parse_copilot(request('account.getQuota'))
    if policy.adapter == 'claude-usage':
        arguments = ['--safe-mode', '--tools', '', '--strict-mcp-config', '--mcp-config',
            '{"mcpServers":{}}', '--no-session-persistence', '--verbose', '--output-format', 'stream-json', '-p', '/usage']
        return parse_claude(*run(manager, home, arguments, end))
    if policy.adapter == 'antigravity-usage':
        return parse_antigravity(*run(manager, home, ['-p', '/usage', '--output-format', 'json'], end))
    raise BudgetError('unsupported')
