"""Admission checks for adapters that share an original native history store."""
from pathlib import Path
from agent_manager.errors import Error


def check_idle(manager, state, source, *, exclude_run=None, native_idle=False):
    """Unknown writers block unless the adapter proved exact native idle ownership.

    Known writers of this conversation and routed pickers always block.
    Call under the registry lock; native_idle is private adapter evidence.
    """
    from agent_manager.profiles.identities import for_environment
    manager.active_runs(state, for_environment(state, source['origin_environment_id']))
    history = Path(source['history_home']).resolve()
    for run_id, run in state['runs'].items():
        if run_id == exclude_run: continue
        store = run.get('source_history_home', run.get('home'))
        routed_picker = (not run.get('thread_id') and state.get('session_routes', {}).get(run.get('environment_id'), {}).get(source['native_id']) == source['ref'])
        if routed_picker or store and Path(store).resolve() == history and (run.get('thread_id') == source['native_id'] or not run.get('thread_id') and not native_idle):
            raise Error('The selected conversation already has an active managed run or unconfirmed launch; stop it before continuing')

