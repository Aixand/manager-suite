"""Shared file-history lifecycle with separate Claude/Copilot native validators."""
from pathlib import Path
import uuid

from agent_manager.errors import Error
from agent_manager.runtime.continuation.base import NativeAdapter


class FileAdapter(NativeAdapter):
    history_mode = 'original-history'
    session_ids = True

    @property
    def native(self):
        raise NotImplementedError

    def availability(self):
        from agent_manager.runtime.native.file_continuation import available
        enabled = available()
        return enabled, None if enabled else 'Claude and Copilot cross-environment continuation currently requires Linux.'

    def discover(self, manager, cwd, *, include_archived=False, include_children=False):
        from agent_manager.profiles.identities import for_environment
        state = manager.state()
        for identifier, environment in state['environments'].items():
            home = Path(environment['home']).resolve()
            known = {row['native_id'] for row in state['sessions'].values() if Path(row['history_home']).resolve() == home}
            for item in self.native.discover(home, known=known):
                yield for_environment(state, identifier), item

    def prepare_history(self, manager, profile, saved, *, args, cwd):
        from agent_manager.runtime.native.file_continuation import prepare
        return prepare(manager, profile, saved, args=args, cwd=cwd)

    def validate_command(self, manager, prepared, arguments):
        self.native.validate_arguments(arguments, prepared.source_session['native_id'])

    def validate_submission(self, manager, record, run_id):
        if record.get('native_continuation'):
            from agent_manager.runtime.native.file_continuation import validate_receipt
            validate_receipt(manager, record['native_continuation'], run_id=run_id)

    def resume(self, manager, profile, native_id):
        from agent_manager.profiles.sessions import for_resume
        from agent_manager.runtime.continuation.coordinator import capture, prepare
        saved = for_resume(manager, profile, native_id)
        return prepare(manager, capture(manager, saved, profile)) if saved is not None else None

    def configure_run(self, manager, profile, args, options, source):
        from agent_manager.runtime.native.file_continuation import requested_identity, check_new_identity
        resume, fresh, picker = requested_identity(manager.launch_args(profile, args, options.get('model')), manager.provider)
        if source is None and resume:
            plan = self.resume(manager, profile, resume)
            if plan:
                source = dict(plan.session)
                options.update(conversation_id=resume, _continuation_plan=plan)
        if not resume and not picker and source is None:
            options['_native_session_id'] = fresh or str(uuid.uuid4())
            check_new_identity(manager, profile, options['_native_session_id'])
            if fresh is None: args = ['--session-id', options['_native_session_id'], *args]
        return list(args), source

    def acquire_session(self, manager, profile, run_id, native_id, source):
        if native_id:
            from agent_manager.runtime.native.file_continuation import acquire_session
            acquire_session(manager, run_id, native_id, source['history_home'] if source else manager.home(profile))

    def terminal_identity(self, manager, profile, run_id, resume_id, source, *, mode):
        native_id = resume_id or (str(uuid.UUID(run_id)) if mode == 'terminal' else None)
        if native_id:
            from agent_manager.runtime.native.file_continuation import check_new_identity
            if not resume_id: check_new_identity(manager, profile, native_id)
            self.acquire_session(manager, profile, run_id, native_id, source)
        return native_id


class ClaudeAdapter(FileAdapter):
    id = mechanism = 'claude-transcript-path'

    @property
    def native(self):
        from agent_manager.runtime.native import claude_sessions
        return claude_sessions

    def validate_source(self, manager, saved, profile):
        path = self.native.transcript(Path(saved['history_home']), saved['native_id'])
        if saved.get('rollout_path') and path != Path(saved['rollout_path']).resolve():
            raise Error('The original Claude transcript path changed; select the session again')

    def claim(self, manager, state, prepared):
        super().claim(manager, state, prepared)
        from agent_manager.runtime.native.file_continuation import check_claude_writers
        check_claude_writers(manager, state, prepared.source_session, prepared.home)


class CopilotAdapter(FileAdapter):
    id = mechanism = 'copilot-session-link'

    @property
    def native(self):
        from agent_manager.runtime.native import copilot_sessions
        return copilot_sessions

    def validate_source(self, manager, saved, profile):
        self.native.event_log(Path(saved['history_home']), saved['native_id'])
