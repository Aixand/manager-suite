"""Codex original-store handoff; native storage and writer locks stay native."""
from agent_manager.runtime.continuation.base import NativeAdapter


class CodexAdapter(NativeAdapter):
    id = mechanism = 'codex-linux-history'
    history_mode = 'original-history'
    sdk_resume_key = 'thread_id'

    def availability(self):
        from agent_manager.runtime.native.codex_continuation import available
        enabled = available()
        return enabled, None if enabled else 'Codex cross-profile continuation requires Linux and bubblewrap (bwrap).'

    def discover(self, manager, cwd, *, include_archived=False, include_children=False):
        from agent_manager.profiles.identities import for_environment
        from agent_manager.runtime.native.codex_history import discover_home
        state = manager.state()
        for identifier, row in state['environments'].items():
            profile = for_environment(state, identifier)
            for item in discover_home(row['home'], profile, cwd, include_archived=include_archived):
                yield profile, dict(native_id=item.id, history_home=item.home, cwd=item.cwd, title=item.title,
                                    updated=item.updated, rollout_path=item.rollout, history_mode=item.history_mode, archived=item.archived)

    def validate_source(self, manager, saved, profile):
        from agent_manager.runtime.native.codex_history import discover_home
        if not discover_home(saved['history_home'], profile, thread_id=saved['native_id']):
            from agent_manager.errors import Error
            raise Error('The original native session is no longer present; a replacement was not created')

    def prepare_history(self, manager, profile, saved, *, args, cwd):
        from agent_manager.runtime.native.codex_continuation import prepare
        return prepare(manager, manager.home(profile), saved, args=args, cwd=cwd)

    def prepare_admission(self, manager, home, source):
        from agent_manager.runtime.native.codex_admission import prepare
        return prepare(manager, home, source)

    def claim(self, manager, state, prepared):
        if prepared.admission is None:
            return super().claim(manager, state, prepared)
        from agent_manager.runtime.continuation.coordination import check_idle
        prepared.admission.validate(manager)
        check_idle(manager, state, prepared.source_session, native_idle=True)

    def validate_submission(self, manager, record, run_id):
        evidence = record.get('native_writer_guard')
        if evidence is None:
            return
        from pathlib import Path
        from agent_manager.runtime.native.codex_admission import WriterGuard
        from agent_manager.runtime.continuation.coordination import check_idle
        home = Path(record['home']).resolve()
        source = dict(provider='codex', origin_environment_id=record['environment_id'],
                      history_home=record['source_history_home'], native_id=record['thread_id'],
                      ref=record['source_session_ref'])
        if (Path(evidence['home']).resolve() != home or Path(source['history_home']).resolve() != home
                or evidence['thread_id'] != source['native_id']):
            from agent_manager.errors import Error
            raise Error('Codex native writer evidence differs from the selected conversation')
        WriterGuard(home, evidence['thread_id'], evidence['program']).validate(manager)
        check_idle(manager, manager.state(), source, exclude_run=run_id, native_idle=True)
