"""Public, read-only execution observation for external integrations."""
import asyncio

from agent_manager.runtime.runs import read_run
from agent_manager.runtime.lifecycle import observe, observation_connection


async def pending(manager, row, run_id, runtime):
    """A native startup hook can precede the launch acknowledgement."""
    owned = runtime is None or row.get('connection') != runtime.connection_options
    client = None
    try:
        async with asyncio.timeout(4):
            if owned:
                import undercurrent
                client = undercurrent.Client(**observation_connection(row))
                await client.connect()
            else: client = runtime
            matches = await client.list(labels={'agent_manager.run_id': run_id})
            if len(matches) == 1:
                session = matches[0]
                return ('terminated' if session.lifecycle in ('stopped', 'exited', 'failed') else 'active', session.id)
    except Exception:
        pass
    finally:
        if owned and client is not None:
            try: await client.close()
            except Exception: pass
    return 'unknown', None


async def execution_status(manager, run_id, *, runtime=None):
    row = await asyncio.to_thread(read_run, manager, run_id)
    state = 'unknown'
    session_id = row.get('session_id')
    if row.get('status') in ('finished', 'failed', 'exited', 'stopped'):
        state = 'terminated'
    elif row.get('runtime') == 'undercurrent' and row.get('session_id'):
        original = runtime if runtime is not None and row.get('connection') == runtime.connection_options else None
        observed = await observe(row, runtime=original)
        state = {'running': 'active', 'finished': 'terminated', 'missing': 'terminated'}.get(observed, 'unknown')
    elif row.get('runtime') == 'undercurrent':
        state, session_id = await pending(manager, row, run_id, runtime)
    # A wrapper PID alone cannot establish whether its child survived a crash.
    # Unfinalized subprocess records therefore remain unknown.
    return dict(schema='agent-manager/execution-observation-1', run_id=run_id,
                provider=manager.provider, environment_id=row.get('environment_id'),
                profile_id=row.get('profile_id'), state=state,
                phase=row.get('status', 'running'), runtime=row.get('runtime', 'subprocess'),
                session_id=session_id, native_session_id=row.get('native_session_id') or row.get('thread_id'))
