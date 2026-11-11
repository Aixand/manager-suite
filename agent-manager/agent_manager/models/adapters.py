"""Fixed native metadata probes. Discovery never submits a user/model turn."""
import json
import re
import subprocess

from agent_manager._version import __version__
from agent_manager.errors import Error
from agent_manager.budgets.models import BudgetError, failure
from agent_manager.budgets.transport import deadline, message, process, Reader, rpc, run
from .schema import ModelError, MAX_CACHE, MAX_MODELS, model, text

REVISION = 'models-adapters-1'
PROVIDERS = {'codex-models': 'codex', 'opencode-models': 'opencode',
    'claude-models': 'claude', 'copilot-models': 'copilot', 'antigravity-models': 'antigravity'}
CLAUDE_ALIASES = (
    ('sonnet', 'Sonnet'), ('opus', 'Opus'), ('haiku', 'Haiku'), ('fable', 'Fable'),
    ('best', 'Best available'), ('sonnet[1m]', 'Sonnet · 1M context'),
    ('opus[1m]', 'Opus · 1M context'), ('opusplan', 'Opus plan / Sonnet execution'))


def codex(rows):
    result = []
    for row in rows:
        variants = [dict(id=item['reasoningEffort'], name=item.get('description'), category='reasoning',
            default=item['reasoningEffort'] == row.get('defaultReasoningEffort'))
            for item in row.get('supportedReasoningEfforts', [])]
        variants.extend(dict(id=item['id'], name=item.get('name'), category='speed') for item in (
            row.get('serviceTiers') or [dict(id=value) for value in row.get('additionalSpeedTiers', [])]))
        result.append(model(row.get('model', row.get('id')), row.get('displayName'),
            description=row.get('description'), default=row.get('isDefault'),
            capabilities={'reasoning': bool(row['supportedReasoningEfforts']) if 'supportedReasoningEfforts' in row else None},
            variants=variants if any(key in row for key in ('supportedReasoningEfforts', 'serviceTiers', 'additionalSpeedTiers')) else None))
    return result


def codex_controls(rows):
    """Adapt the shared public snapshot for dependent native setting choices."""
    return [dict(id=row['id'], model=row['id'], displayName=row['name'], isDefault=row['default'],
        supportedReasoningEfforts=[dict(reasoningEffort=v['id']) for v in row['variants'] or [] if v['category'] == 'reasoning'],
        defaultReasoningEffort=next((v['id'] for v in row['variants'] or [] if v['category'] == 'reasoning' and v['default']), ''),
        additionalSpeedTiers=[v['id'] for v in row['variants'] or [] if v['category'] == 'speed']) for row in rows]


def opencode(payload):
    entries = payload.get('data')
    if not isinstance(entries, list): raise ModelError('invalid-response')
    result = []
    for row in entries:
        provider, key = text(row['providerID']), text(row['id'])
        costs = row.get('cost')
        prices = None if costs is None else [dict(unit='usd-per-million-tokens', input=t.get('input'),
            output=t.get('output'), cache_read=t.get('cache', {}).get('read'),
            cache_write=t.get('cache', {}).get('write'), context_over=(t.get('tier') or {}).get('size')) for t in costs]
        caps, limits = row.get('capabilities') or {}, row.get('limit') or {}
        inputs = caps.get('input')
        if inputs is not None and (not isinstance(inputs, list) or any(not isinstance(item, str) for item in inputs)):
            raise ModelError('invalid-response')
        variants = row.get('variants')
        if variants is not None and not isinstance(variants, list): raise ModelError('invalid-response')
        result.append(model(provider + '/' + key, row.get('name'), provider_id=provider,
            resolved_id=row.get('modelID'), availability='listed' if row.get('enabled', True) else 'restricted',
            capabilities={'tools': caps.get('tools'), 'vision': 'image' in inputs if inputs is not None else None},
            limits={'context_tokens': limits.get('context'), 'output_tokens': limits.get('output')},
            variants=None if variants is None else [dict(id=v['id'], name=v['id'], category='variant') for v in variants], pricing=prices))
    return result


def copilot(rows):
    result = []
    for row in rows:
        capabilities = row.get('capabilities') or {}
        supports, limits = capabilities.get('supports') or {}, capabilities.get('limits') or {}
        billing = row.get('billing') or {}
        multiplier = billing.get('multiplier')
        prices = [] if multiplier is None else [dict(unit='premium-request-multiplier', multiplier=multiplier)]
        tokens = billing.get('tokenPrices')
        if tokens is not None:
            for tier in (tokens, tokens.get('longContext')):
                if tier is None: continue
                # The native model list may omit billing currency. Preserve
                # the reported prices and batch size with unit=null then.
                prices.append(dict(unit=None, input=tier.get('inputPrice'), output=tier.get('outputPrice'),
                    cache_read=tier.get('cacheReadPrice', tier.get('cachePrice')),
                    cache_write=tier.get('cacheWritePrice'), cache_write_1h=tier.get('cacheWrite1hPrice'),
                    batch_size=tokens.get('batchSize'), max_prompt_tokens=tier.get('maxPromptTokens', tier.get('contextMax'))))
        result.append(model(row['id'], row.get('name'),
            availability='restricted' if (row.get('policy') or {}).get('state') in ('disabled', 'unconfigured') else 'listed',
            capabilities={'vision': supports.get('vision'), 'reasoning': supports.get('reasoning')},
            limits={'context_tokens': limits.get('max_context_window_tokens'), 'output_tokens': limits.get('max_output_tokens')},
            pricing=prices or None))
    return result


def claude(manager, home, cwd, end):
    # Same control initialization used by the official SDK, without a user
    # message, session persistence, MCP servers, tools or a model request.
    arguments = ['--print', '--verbose', '--input-format', 'stream-json', '--output-format', 'stream-json',
        '--no-session-persistence', '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}']
    reader = None
    try:
        with process(manager, home, arguments, stdin=subprocess.PIPE, cwd=cwd) as child:
            reader = Reader(child.stdout, lambda stream: message(stream, False))
            child.stdin.write(json.dumps(dict(type='control_request', request_id='models',
                request=dict(subtype='initialize', hooks=None))).encode() + b'\n')
            child.stdin.flush()
            size = 0
            while True:
                item = reader.get(end)
                if item is None: raise ModelError('unavailable')
                size += len(json.dumps(item))
                if size > MAX_CACHE: raise ModelError('invalid-response')
                if item.get('type') == 'control_request': raise ModelError('unsupported')
                if item.get('type') != 'control_response': continue
                response = item.get('response', {})
                if response.get('request_id') != 'models': continue
                if response.get('subtype') != 'success': raise ModelError(failure(response).code)
                info = response.get('response', {})
                rows = info.get('models')
                if rows is None:
                    return [model(key, name, kind='preset' if key == 'opusplan' else 'alias',
                        availability='unknown') for key, name in CLAUDE_ALIASES], 'claude-aliases'
                if not isinstance(rows, list): raise ModelError('invalid-response')
                return [model(row['value'], row.get('displayName'), description=row.get('description'),
                    kind='preset' if row['value'] == 'opusplan' else 'alias' if row['value'] in dict(CLAUDE_ALIASES) else 'model',
                    availability='unknown') for row in rows], 'claude-initialize'
    finally:
        if reader: reader.close()


def read(manager, home, cwd, policy):
    end = deadline(policy.timeout)
    try:
        args = ['--binary-version'] if manager.provider == 'copilot' else ['--version']
        output, code = run(manager, home, args, end, cwd=cwd)
        match = re.search(rb'(?:^|\s)v?(\d+\.\d+\.\d+)(?:\s|$)', output)
        if code or not match or match[1].decode() not in policy.versions: raise ModelError('unsupported')
        version = match[1].decode()
        if policy.adapter == 'codex-models':
            with rpc(manager, home, ['app-server', '--listen', 'stdio://'], end, cwd=cwd) as request:
                request('initialize', {'clientInfo': {'name': 'agent_manager_models', 'version': __version__},
                    'capabilities': {'experimentalApi': True}})
                request('initialized', notification=True)
                rows, cursor, seen = [], None, set()
                for _ in range(20):
                    page = request('model/list', dict(limit=100, includeHidden=False, **({'cursor': cursor} if cursor else {})))
                    data = page.get('data')
                    if not isinstance(data, list): raise ModelError('invalid-response')
                    rows.extend(data)
                    if len(rows) > MAX_MODELS: raise ModelError('invalid-response')
                    cursor = page.get('nextCursor')
                    if not cursor: return codex(rows), policy.adapter, version
                    if cursor in seen: raise ModelError('invalid-response')
                    seen.add(cursor)
                raise ModelError('invalid-response')
        if policy.adapter == 'copilot-models':
            args = ['--headless', '--stdio', '--no-auto-update', '--disable-client-telemetry',
                '--disable-builtin-mcps', '--no-custom-instructions', '--sync-session=off']
            with rpc(manager, home, args, end, framed=True, cwd=cwd) as request:
                status = request('status.get')
                if status.get('version') != version or status.get('protocolVersion') != 3: raise ModelError('unsupported')
                return copilot(request('models.list')['models']), policy.adapter, version
        if policy.adapter == 'claude-models':
            rows, source = claude(manager, home, cwd, end)
            return rows, source, version
        if policy.adapter == 'antigravity-models':
            output, code = run(manager, home, ['models'], end, cwd=cwd)
            if code: raise ModelError('unavailable')
            rows = {}
            for line in re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', output.decode()).splitlines():
                key, tab, name = line.partition('\t')
                if tab and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,255}', key.strip()):
                    rows.setdefault(key.strip(), model(key.strip(), name.strip()))
            if not rows: raise ModelError('invalid-response')
            return list(rows.values()), policy.adapter, version
        if policy.adapter == 'opencode-models':
            from agent_manager.providers.opencode import catalog
            from agent_manager.budgets.transport import remaining
            return opencode(catalog(manager, home, {}, cwd=cwd, startup_timeout=remaining(end), end=end)), policy.adapter, version
        raise ModelError('unsupported')
    except BudgetError as error:
        raise ModelError(error.code) from None
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError):
        raise ModelError('invalid-response') from None
    except TimeoutError:
        raise ModelError('timeout') from None
    except OSError:
        raise ModelError('unavailable') from None
    except Error as error:
        from agent_manager.providers.opencode import CatalogError
        raise ModelError(error.code if isinstance(error, CatalogError) else 'unavailable') from None
