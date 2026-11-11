"""OpenCode checkpoint copies; source turn activity is never an admission gate."""
from pathlib import Path

from agent_manager.errors import Error
from agent_manager.runtime.continuation.base import NativeAdapter


class OpenCodeAdapter(NativeAdapter):
    id = mechanism = 'opencode-session-copy'
    history_mode = 'copy'

    def availability(self): return True, None

    def discover(self, manager, cwd, *, include_archived=False, include_children=False):
        from agent_manager.profiles.identities import for_environment
        from agent_manager.providers.opencode import sessions
        state = manager.state()
        for identifier, row in state['environments'].items():
            profile = for_environment(state, identifier)
            for item in sessions(manager, Path(row['home']), profile, cwd,
                    include_archived=include_archived, include_children=include_children):
                yield profile, dict(native_id=item.id, history_home=item.home, cwd=item.cwd, title=item.title, updated=item.updated,
                    archived=item.archived, parent_id=item.parent_id)

    def logical_source(self, manager, value, rows):
        logical = [row for row in rows if value == row.get('conversation_id')]
        if logical:
            graph = manager.state()['opencode_continuation']['conversations'][value]
            return next(row for row in logical if row['ref'] == graph['origin_ref'])

    def validate_source(self, manager, saved, profile):
        from agent_manager.providers.opencode import sessions
        if not any(row.id == saved['native_id'] for row in sessions(manager, Path(saved['history_home']), profile)):
            raise Error('The original native session is no longer present; a replacement was not created')

    def materialize(self, manager, request):
        saved = dict(request.source)
        if manager.provider_spec.sessions.cross_environment_resume and (saved.get('conversation_id') or Path(saved['history_home']).resolve() != request.home):
            from agent_manager.profiles.opencode_copies import prepare
            return prepare(manager, saved, request.target, logical=request.logical)
        return saved

    def claim(self, manager, state, prepared):
        from agent_manager.profiles.identities import for_environment
        self.validate_source(manager, prepared.source_session, for_environment(state, prepared.environment_id))

    def provenance(self, session):
        return {'opencode_copy': dict(session)} if session.get('conversation_id') else {}

    def validate_command(self, manager, prepared, arguments):
        from agent_manager.runtime.native.opencode_transfer import validate_arguments
        validate_arguments(arguments, prepared.source_session['native_id'])
        if prepared.source_session.get('conversation_id'):
            from agent_manager.profiles.opencode_copies import validate_launch
            validate_launch(manager, prepared.source_session, prepared.environment_id)

    def validate_submission(self, manager, record, run_id):
        if record.get('opencode_copy'):
            from agent_manager.profiles.opencode_copies import validate_launch
            validate_launch(manager, record['opencode_copy'], record['environment_id'])

    def resume(self, manager, profile, native_id):
        from agent_manager.profiles.sessions import for_resume, select
        from agent_manager.runtime.continuation.coordinator import prepare
        saved = for_resume(manager, profile, native_id)
        return prepare(manager, select(manager, saved['ref'], profile)) if saved is not None else None

    def configure_run(self, manager, profile, args, options, source):
        if source is None:
            from agent_manager.runtime.native.opencode_transfer import requested_session, replace_session
            native_id = requested_session(manager.launch_args(profile, args, options.get('model')))
            plan = self.resume(manager, profile, native_id) if native_id else None
            if plan:
                source = dict(plan.session)
                args = replace_session(args, native_id, plan.native_id)
                options.update(conversation_id=plan.native_id, _continuation_plan=plan)
        return list(args), source

    def picker(self, manager, rows):
        from agent_manager.profiles.identities import for_environment
        state = manager.state()
        return ['Session', 'Environment', 'Copy', 'Native ID', 'Working directory'], [
            (row['title'], str(for_environment(state, row['origin_environment_id'])),
             row.get('copy_status', 'unobserved') + (' (unavailable)' if row.get('copy_available') is False else ''),
             row['native_id'], row['cwd']) for row in rows]
