"""Seal an idle worker for sign-in without treating a finished turn as idle."""
import asyncio
import urllib.parse

from .models import ConversationError, operation_key


def local_blockers(core):
    result = []
    for kind, values in (("active_turns", core.active), ("inflight_operations", core.inflight)):
        if values:
            result.append(dict(kind=kind, count=len(values)))
    pending = sum(live['record']['state'] in ('pending', 'reserved', 'submitted')
                  for live in core.requests.values())
    if pending:
        result.append(dict(kind='requests', count=pending))
    for session in core.sessions.values():
        items = core.message_queue.get(session)['items']
        if any(row['status'] not in ('delivered', 'removed') for row in items):
            result.append(dict(kind='queued_work', session_id=session['session_id']))
    return result


async def native_blockers(core):
    # Enumerate all loaded threads, including native children. Missing provider
    # APIs are uncertainty, never evidence that it is safe to stop the process.
    from .background import pages
    adapter = core.adapter
    if hasattr(adapter, 'http'):
        return await opencode_blockers(adapter, [session['native_session_id'] for session in core.sessions.values() if session.get('native_session_id')])
    if not hasattr(adapter, 'rpc') or not hasattr(adapter, 'controls'):
        return [dict(kind='activity_unavailable')]
    result = []
    try:
        async with asyncio.timeout(15):
            remote = await adapter.rpc('remoteControl/status/read', {})
            if remote.get('status') != 'disabled':
                result.append(dict(kind='remote_control'))
            for identity in await pages(adapter, 'thread/loaded/list', {}):
                thread = (await adapter.rpc('thread/read', dict(threadId=identity, includeTurns=False)))['thread']
                status = thread.get('status')
                kind = status.get('type') if isinstance(status, dict) else status
                if kind not in ('idle', 'notLoaded'):
                    result.append(dict(kind='active_thread' if kind == 'active' else 'unknown_thread'))
                if thread.get('ephemeral'):
                    result.append(dict(kind='ephemeral_thread'))
                for method, reason in [('thread/backgroundTerminals/list', 'background_commands'),
                                       ('thread/queue/list', 'queued_work')]:
                    if await pages(adapter, method, dict(threadId=identity)):
                        result.append(dict(kind=reason))
                goal = await adapter.rpc('thread/goal/get', dict(threadId=identity))
                if 'goal' not in goal:
                    raise ValueError('Missing goal observation')
                if goal['goal'] and goal['goal'].get('status') not in ('complete', 'paused'):
                    result.append(dict(kind='active_goal'))
    except Exception:
        result.append(dict(kind='activity_unavailable'))
    return result


async def opencode_blockers(adapter, bound_sessions=()):
    """Read the private OpenCode server's active sessions, inboxes and PTYs."""
    from .background import opencode_agents, opencode_processes
    result = []
    try:
        async with asyncio.timeout(15):
            active = (await adapter.http('GET', '/api/session/active'))['data']
            if active:
                return [dict(kind='active_thread', count=len(active))]
            roots, cursor, seen = [], None, set()
            for _ in range(100):
                query = urllib.parse.urlencode(dict(limit=100, **({'cursor': cursor} if cursor else {})))
                page = await adapter.http('GET', '/api/session?' + query)
                roots.extend(row['id'] for row in page['data'])
                cursor = (page.get('cursor') or {}).get('next')
                if not cursor:
                    break
                if cursor in seen:
                    raise ValueError('Repeated session cursor')
                seen.add(cursor)
            else:
                raise ValueError('Incomplete session inventory')
            roots = list(dict.fromkeys([*roots, *bound_sessions]))
            identities = set(roots)
            for root in roots:
                for agent in await opencode_agents(adapter, root):
                    identities.add(agent['id'])
                    if agent['status'] not in ('idle', 'completed', 'failed', 'cancelled'):
                        result.append(dict(kind='active_thread'))
            for identity in identities:
                path = '/api/session/' + urllib.parse.quote(identity, safe='')
                for suffix, reason in [('/inbox', 'queued_work'), ('/permission', 'requests'), ('/form', 'requests')]:
                    if (await adapter.http('GET', path + suffix))['data']:
                        result.append(dict(kind=reason))
                if any(row['status'] not in ('exited', 'stopped', 'completed', 'failed', 'cancelled')
                       for row in await opencode_processes(adapter, identity)):
                    result.append(dict(kind='background_commands'))
            if (await adapter.http('GET', '/api/session/active'))['data']:
                result.append(dict(kind='active_thread'))
    except Exception:
        result.append(dict(kind='activity_unavailable'))
    return result


async def prepare(core, operation_id):
    operation_key(operation_id)
    saved = core.store.get('worker-drain', core.ref['connection_id'])
    if core.draining and (not saved or saved.get('signin_operation') != operation_id):
        return dict(ready=False, blockers=[dict(kind='other_maintenance')])
    blockers = local_blockers(core)
    if blockers:
        if saved and saved.get('signin_operation') == operation_id:
            cancel(core, operation_id)
        return dict(ready=False, blockers=blockers)
    # Close admission before awaiting provider inspection. Already-dispatched
    # operations are counted again afterward. This seal survives coordinator loss.
    core.draining = True
    core.store.put('worker-drain', core.ref['connection_id'],
                   dict(epoch=core.epoch, sealed=True, signin_operation=operation_id))
    keep_sealed = False
    try:
        blockers = await native_blockers(core)
        blockers.extend(local_blockers(core))
        if core.lost:
            blockers.append(dict(kind='transport_unavailable'))
        keep_sealed = not blockers
        return dict(ready=keep_sealed, blockers=blockers)
    finally:
        if not keep_sealed:
            cancel(core, operation_id)


async def activity(core):
    """Read-only preflight; an active parent retains child/approval admission."""
    blockers = local_blockers(core)
    if not blockers:
        blockers = await native_blockers(core)
        blockers.extend(local_blockers(core))
    if core.lost:
        blockers.append(dict(kind='transport_unavailable'))
    return dict(ready=not blockers, blockers=blockers)


def cancel(core, operation_id):
    operation_key(operation_id)
    saved = core.store.get('worker-drain', core.ref['connection_id'])
    if not saved and not core.draining:
        return dict(draining=False)
    if not saved or saved.get('signin_operation') != operation_id:
        raise ConversationError('conflict', 'This sign-in does not own the worker drain')
    # Store supports JSON null; Core treats a null record as no drain on restart.
    core.store.put('worker-drain', core.ref['connection_id'], None)
    core.draining = False
    return dict(draining=False)
