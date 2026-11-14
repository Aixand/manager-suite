"""Provider-owned background inspection; never resumes a thread or starts work."""
import asyncio
import json
import urllib.parse
from .models import ConversationError


def unsupported(error):
    return getattr(error, 'code', None) == 'unsupported' or any(word in str(error).lower() for word in ('unknown method', 'method not found', 'unknown variant', 'unsupported request'))


def bounded_preview(items):
    result, size = [], 0
    for item in reversed(items):
        item = {**item, 'text': item.get('text', '')[-65536:]}
        length = len(json.dumps(item).encode())
        if result and (size + length > 512 * 1024 or len(result) >= 100):
            break
        result.append(item)
        size += length
    return dict(items=list(reversed(result)), truncated=len(result) < len(items))


async def pages(adapter, method, params):
    rows, cursor, seen = [], None, set()
    for _ in range(20):
        page = await adapter.rpc(method, {**params, 'limit': 100, **({'cursor': cursor} if cursor else {})})
        if not isinstance(page.get('data'), list):
            raise ConversationError('validation', 'Provider returned an incomplete activity list')
        rows.extend(page['data'])
        cursor = page.get('nextCursor')
        if not cursor:
            return rows
        if cursor in seen:
            break
        seen.add(cursor)
    raise ConversationError('validation', 'Background activity pagination did not finish')


async def codex_agents(adapter, identity):
    rows = await pages(adapter, 'thread/list', {
        'sourceKinds': ['subAgent', 'subAgentReview', 'subAgentCompact', 'subAgentThreadSpawn', 'subAgentOther'],
        'modelProviders': [],
    })
    # Some supported Codex versions silently ignore relationship query fields.
    # Verify ancestry locally before exposing any thread or allowing inspection.
    parents = {}
    for row in rows:
        source = row.get('source')
        sub = source.get('subAgent') if isinstance(source, dict) else None
        spawn = sub.get('thread_spawn', {}) if isinstance(sub, dict) else {}
        parents[row['id']] = row.get('parentThreadId') or spawn.get('parent_thread_id')
    descendants = {identity}
    for _ in rows:
        added = {key for key, parent in parents.items() if parent in descendants} - descendants
        if not added:
            break
        descendants.update(added)
    result = []
    for row in rows:
        if row['id'] not in descendants or row['id'] == identity:
            continue
        native = row.get('status') or {}
        kind = native.get('type') if isinstance(native, dict) else native
        flags = native.get('activeFlags', []) if isinstance(native, dict) else []
        waiting = bool(set(flags) & {'waitingOnApproval', 'waitingOnUserInput'})
        if len(result) >= 200:
            raise ConversationError('validation', 'Background agent tree exceeds inspection limit')
        result.append(dict(id=row['id'], parentId=parents[row['id']],
            title=(row.get('agentNickname') or row.get('name') or row.get('preview') or 'Background agent')[:160],
            preview=row.get('preview', '')[:1000], status='waiting' if waiting else {
                'active': 'running', 'idle': 'idle', 'systemError': 'failed', 'notLoaded': 'unknown',
            }.get(kind, 'unknown'), needsInput=waiting, canInspect=True, canStop=False, source='provider'))
    return result


async def codex_processes(adapter, identity):
    rows = await pages(adapter, 'thread/backgroundTerminals/list', {'threadId': identity})
    return [dict(id=str(row['processId']), itemId=row.get('itemId'), command=row.get('command', ''),
        cwd=row.get('cwd', ''), status='running', canStop=True, source='provider') for row in rows]


async def observed_processes(adapter, identity):
    from .native_codex import read_thread
    thread = (await read_thread(adapter.rpc, identity))['thread']
    return [dict(id=str(item.get('processId') or item['id']), itemId=item['id'], command=item.get('command', ''),
        cwd=item.get('cwd', ''), status='running', canStop=False, source='provider')
        for turn in thread.get('turns', []) for item in turn.get('items', [])
        if item.get('type') == 'commandExecution' and item.get('status') == 'inProgress']


async def inspect_activity(adapter, session):
    if hasattr(adapter, 'http'):
        return await opencode_activity(adapter, session['native_session_id'])
    if not hasattr(adapter, 'controls') or not hasattr(adapter, 'rpc'):
        return dict(agents=[], processes=[], agentsAvailability='unsupported', processesAvailability='unsupported')
    identity = session['native_session_id']
    agents, processes = await asyncio.gather(codex_agents(adapter, identity), codex_processes(adapter, identity), return_exceptions=True)
    result = {}
    for name, value in (('agents', agents), ('processes', processes)):
        if isinstance(value, Exception):
            if name == 'processes' and unsupported(value):
                try:
                    result[name] = await observed_processes(adapter, identity)
                    result[name + 'Availability'] = 'limited'
                    continue
                except Exception:
                    pass
            result[name] = []
            result[name + 'Availability'] = 'unsupported' if unsupported(value) else 'unavailable'
        else:
            result[name], result[name + 'Availability'] = value, 'ready'
    return result


async def inspect_agent(adapter, session, agent_id):
    if hasattr(adapter, 'http'):
        agents = await opencode_agents(adapter, session['native_session_id'])
        if not any(row['id'] == agent_id for row in agents):
            raise ConversationError('conflict', 'Agent is not a descendant of this conversation')
        messages = await adapter.messages(agent_id, first_page=True)
        items = []
        for message in messages:
            for index, part in enumerate(message.get('content', [])):
                if part.get('type') == 'text':
                    items.append(dict(id=message['id'] + ':' + str(index), type='agentMessage' if message.get('type') == 'assistant' else 'userMessage', text=part.get('text', '')))
        return bounded_preview(items)
    if not hasattr(adapter, 'rpc'):
        raise ConversationError('unsupported', 'Background agent inspection is unavailable')
    agents = await codex_agents(adapter, session['native_session_id'])
    if not any(row['id'] == agent_id for row in agents):
        raise ConversationError('conflict', 'Agent is not a descendant of this conversation')
    from .native_codex import read_thread
    from .transcript import codex_items
    return bounded_preview(codex_items(await read_thread(adapter.rpc, agent_id), {})['items'])


async def stop_process(adapter, session, process_id):
    identity = session['native_session_id']
    if hasattr(adapter, 'http'):
        processes = await opencode_processes(adapter, identity)
        if not any(row['id'] == process_id and row['canStop'] for row in processes):
            raise ConversationError('conflict', 'Process is no longer running in this conversation')
        await adapter.http('DELETE', '/api/experimental/persistent-pty/' + urllib.parse.quote(process_id, safe=''))
        return dict(terminated=True)
    processes = await codex_processes(adapter, identity)
    if not any(row['id'] == process_id for row in processes):
        raise ConversationError('conflict', 'Process is no longer running in this conversation')
    return await adapter.rpc('thread/backgroundTerminals/terminate', {'threadId': identity, 'processId': process_id})


async def opencode_agents(adapter, identity):
    pending, result, seen = [identity], [], {identity}
    active = (await adapter.http('GET', '/api/session/active'))['data']
    while pending:
        parent = pending.pop(0)
        cursor, cursors = None, set()
        for _ in range(20):
            query = urllib.parse.urlencode(dict(parentID=parent, limit=100, **({'cursor': cursor} if cursor else {})))
            page = await adapter.http('GET', '/api/session?' + query)
            for row in page['data']:
                if row.get('parentID') != parent or row['id'] in seen:
                    continue
                seen.add(row['id'])
                if len(seen) > 200:
                    raise ConversationError('validation', 'Background agent tree exceeds inspection limit')
                pending.append(row['id'])
                outcome = row.get('outcome')
                result.append(dict(id=row['id'], parentId=parent, title=row.get('title') or 'Background agent', preview='',
                    status='running' if row['id'] in active else {'succeeded': 'completed', 'failed': 'failed', 'interrupted': 'cancelled'}.get(outcome, 'idle'),
                    needsInput=False, canInspect=True, canStop=False, source='provider'))
            cursor = (page.get('cursor') or {}).get('next')
            if not cursor:
                break
            if cursor in cursors:
                raise ConversationError('validation', 'Background agent pagination repeated')
            cursors.add(cursor)
        else:
            raise ConversationError('validation', 'Background agent pagination incomplete')
    return result


async def opencode_processes(adapter, identity):
    rows = (await adapter.http('GET', '/api/experimental/session/' + urllib.parse.quote(identity, safe='') + '/terminal'))['data']
    return [dict(id=row['id'], command=' '.join([row['command'], *row.get('args', [])]), cwd=row['cwd'],
        status=row['status'], canStop=row['status'] == 'running', source='provider')
        for row in rows if row.get('sessionID') == identity]


async def opencode_activity(adapter, identity):
    values = await asyncio.gather(opencode_agents(adapter, identity), opencode_processes(adapter, identity), return_exceptions=True)
    result = {}
    for name, value in zip(('agents', 'processes'), values):
        result[name] = [] if isinstance(value, Exception) else value
        result[name + 'Availability'] = 'unavailable' if isinstance(value, Exception) else 'ready'
    return result


async def inspect_process(adapter, session, process_id):
    identity = session['native_session_id']
    if hasattr(adapter, 'http'):
        rows = await opencode_processes(adapter, identity)
        if not any(row['id'] == process_id for row in rows):
            raise ConversationError('conflict', 'Process does not belong to this conversation')
        value = (await adapter.http('GET', '/api/experimental/persistent-pty/' + urllib.parse.quote(process_id, safe='') + '/snapshot'))['data']
        if value['info'].get('sessionID') != identity:
            raise ConversationError('conflict', 'Process ownership changed')
        return dict(text=value['text'][-65536:])
    try:
        rows = await codex_processes(adapter, identity)
    except Exception as error:
        if not unsupported(error):
            raise
        rows = await observed_processes(adapter, identity)
    row = next((row for row in rows if row['id'] == process_id), None)
    if row is None:
        raise ConversationError('conflict', 'Process is no longer visible in this conversation')
    from .native_codex import read_thread
    thread = (await read_thread(adapter.rpc, identity))['thread']
    for turn in thread.get('turns', []):
        for item in turn.get('items', []):
            if item.get('id') == row.get('itemId'):
                return dict(text=(item.get('aggregatedOutput') or '')[-65536:])
    return dict(text='')
