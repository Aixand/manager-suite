"""Common selection, preparation and launch validation for CLI and SDK callers."""
from pathlib import Path

from agent_manager.errors import Error
from agent_manager.runtime.continuation.contracts import ContinuationRequest, ContinuationPlan
from agent_manager.runtime.continuation.registry import for_manager


def resolve_request(manager, value, profile=None):
    """Refresh public native metadata, then select without changing native history."""
    from agent_manager.profiles.sessions import listing, select, require_retained_resume
    require_retained_resume(manager)
    listing(manager)
    return select(manager, value, profile)


def capture(manager, saved, profile, *, logical=False):
    from agent_manager.profiles.sessions import check_reference, require_retained_resume, validate_execution_home
    require_retained_resume(manager)
    check_reference(manager, saved)
    target = manager.resolve_profile(profile)
    home = manager.home(target).resolve()
    validate_execution_home(manager, saved, home)
    return ContinuationRequest(manager.provider, for_manager(manager).id, saved, target, home, logical)


def prepare(manager, request):
    """Explicit mutation boundary; native operations retain their own journals."""
    from agent_manager.profiles.sessions import check_reference, validate_source, validate_execution_home
    from agent_manager.providers.installation.software import require_enabled
    require_enabled(manager)
    adapter = for_manager(manager)
    if request.provider != manager.provider or request.adapter_id != adapter.id:
        raise Error('Continuation provider or adapter changed; select the session again')
    target = manager.resolve_profile(request.target)
    if manager.home(target).resolve() != request.home:
        raise Error('The destination environment home changed')
    check_reference(manager, request.source)
    validate_execution_home(manager, request.source, request.home)
    saved = adapter.materialize(manager, request)
    check_reference(manager, saved)
    validate_source(manager, saved, target)
    return ContinuationPlan(request, saved, adapter.history_mode)


def configure_run(manager, profile, args, options, source):
    args, source = for_manager(manager).configure_run(manager, profile, args, options, source)
    return args, source


def prepare_history(manager, profile, source, *, args, cwd, thread_id, plan=None):
    from agent_manager.profiles.sessions import check_reference, validate_execution_home, validate_source
    check_reference(manager, source)
    if thread_id != source['native_id']:
        raise Error('Continuation identity differs from the original native session')
    if Path(cwd).resolve() != Path(source['cwd']).resolve():
        raise Error('Continuation must use the original session working directory')
    home = manager.home(profile).resolve()
    if plan is not None:
        if (plan.target.environment_id != profile.environment_id or plan.request.home != home
                or dict(plan.session) != source or plan.request.provider != manager.provider
                or plan.request.adapter_id != for_manager(manager).id
                or plan.history_mode != for_manager(manager).history_mode):
            raise Error('Prepared continuation differs from the captured launch')
    validate_execution_home(manager, source, home)
    validate_source(manager, source, profile)
    if Path(source['history_home']).resolve() != home:
        return for_manager(manager).prepare_history(manager, profile, source, args=args, cwd=cwd)


def claim(manager, state, prepared):
    from agent_manager.profiles.sessions import check_reference
    check_reference(manager, prepared.source_session, state=state)
    for_manager(manager).claim(manager, state, prepared)


def provenance(adapter, source, view, plan=None):
    result = dict(adapter.provenance(source), source_session_ref=source['ref'],
                  source_history_home=source['history_home'], origin_environment_id=source['origin_environment_id'])
    if hasattr(view, 'receipt'): result['native_continuation'] = view.receipt
    if plan is not None: result['continuation'] = plan.receipt
    return result


def validate_command(manager, prepared, arguments):
    if prepared and prepared.source_session:
        for_manager(manager).validate_command(manager, prepared, arguments)


def validate_submission(manager, record, run_id):
    receipt = record.get('continuation')
    if receipt is not None:
        if not isinstance(receipt, dict):
            raise Error('Invalid captured continuation receipt')
        adapter = for_manager(manager)
        state = manager.state()
        original = state['sessions'].get(receipt.get('requested_session_ref'))
        session = state['sessions'].get(receipt.get('session_ref'))
        if (receipt.get('schema') != 1 or receipt.get('adapter') != adapter.id
                or receipt.get('history_mode') != adapter.history_mode
                or receipt.get('environment_id') != record.get('environment_id')
                or receipt.get('session_ref') != record.get('source_session_ref')
                or receipt.get('native_id') != record.get('thread_id')
                or original is None or session is None
                or original['origin_environment_id'] != receipt.get('origin_environment_id')
                or session['native_id'] != receipt.get('native_id')
                or session['history_home'] != record.get('source_history_home')
                or session['origin_environment_id'] != record.get('origin_environment_id')
                or session['provider'] != manager.provider or original['provider'] != manager.provider):
            raise Error('The captured continuation receipt changed; refresh before launching')
    for_manager(manager).validate_submission(manager, record, run_id)
