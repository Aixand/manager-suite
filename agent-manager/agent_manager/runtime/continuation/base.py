"""Default same-home behavior for native providers without a history adapter."""
from agent_manager.errors import Error


class NativeAdapter:
    """Same-home defaults and the lifecycle extension points for native mechanisms.

    Discovery/validation only inspect metadata. Materialization owns journaled
    native side effects and its own leases. History preparation runs under the
    destination environment gate. Claim and submission run under the registry
    lock; these stages validate prepared work without importing another copy.
    Native wrappers retain final command validation and child-lifetime locks.
    """
    id = 'native'
    history_mode = 'native-only'
    mechanism = None
    sdk_resume_key = 'resume_id'
    session_ids = False

    def availability(self): return False, 'No native cross-profile continuation adapter is qualified for this provider.'

    def discover(self, manager, cwd, *, include_archived=False, include_children=False):
        from agent_manager.runtime.continuation.registry import storage_adapter
        adapter = storage_adapter(manager.provider_spec.sessions.strategy)
        return adapter.discover(manager, cwd, include_archived=include_archived, include_children=include_children) if adapter else ()

    def logical_source(self, manager, value, rows): return None

    def validate_source(self, manager, saved, profile):
        from agent_manager.runtime.continuation.registry import storage_adapter
        adapter = storage_adapter(manager.provider_spec.sessions.strategy)
        if adapter: adapter.validate_source(manager, saved, profile)

    def materialize(self, manager, request): return dict(request.source)

    def prepare_history(self, manager, profile, saved, *, args, cwd):
        raise Error('No native cross-environment history adapter exists for this provider')

    def prepare_admission(self, manager, home, source): return None

    def claim(self, manager, state, prepared):
        from agent_manager.runtime.continuation.coordination import check_idle
        check_idle(manager, state, prepared.source_session)
        if hasattr(prepared.history_view, 'validate'): prepared.history_view.validate(manager)

    def validate_command(self, manager, prepared, arguments): pass

    def provenance(self, session): return {}

    def validate_submission(self, manager, record, run_id): pass

    def resume(self, manager, profile, native_id): return None

    def configure_run(self, manager, profile, args, options, source): return list(args), source

    def acquire_session(self, manager, profile, run_id, native_id, source): pass

    def terminal_identity(self, manager, profile, run_id, resume_id, source, *, mode): return resume_id

    def picker(self, manager, rows):
        return ['Session', 'Working directory'], [(row['title'], row['cwd']) for row in rows]
