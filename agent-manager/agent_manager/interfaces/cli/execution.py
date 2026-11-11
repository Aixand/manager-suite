"""Internal native terminal execution for CLI commands, outside the public SDK."""
import json
import os
import uuid

from agent_manager.errors import Error
from agent_manager.providers.configuration import scoped as configuration_scope
from agent_manager.storage.files import atomic
from agent_manager.storage.locks import locks


@configuration_scope
def run(manager, agent, args, **options):
    directory = options.pop('cwd', None)
    if directory is not None:
        from agent_manager.runtime.execution import using_directory
        with using_directory(directory):
            return run(manager, agent, args, **options)
    agent = manager.resolve_profile(agent)
    from agent_manager.runtime.native.configuration import normalize, apply as apply_native_config
    options['native_config'] = normalize(manager, options.get('native_config'))
    from agent_manager.providers.installation.software import require_enabled
    require_enabled(manager)
    source = options.pop('_source_session', None)
    from agent_manager.runtime.continuation import coordinator
    args, source = coordinator.configure_run(manager, agent, args, options, source)
    continuation_plan = options.pop('_continuation_plan', None)
    from agent_manager.runtime.native.arguments import resume_id
    thread_id = options.get('conversation_id') or options.get('_native_session_id')
    if manager.provider == 'codex': thread_id = thread_id or resume_id(args)
    from agent_manager.runtime import execution
    from contextlib import nullcontext
    with execution.using_directory(source['cwd']) if source else nullcontext():
        with execution.launch_home(manager, agent, args=apply_native_config(manager, manager.launch_args(agent, args, options.get('model')), options['native_config']),
                cwd=manager.working_directory(), thread_id=thread_id,
                source_session=source, continuation_plan=continuation_plan) as plan:
            return _run(manager, agent, args, _home_plan=plan, **options)

def _run(manager, agent, args, *, validate=None,
         conversation_id=None, model=None, labels=None, metadata=None,
         inherit_labels=True, inherit_metadata=True, _home_plan=None,
         _native_session_id=None, native_config=None):
    from agent_manager.runtime.context import ThreadStore, attributes, defaults, require_metadata
    from agent_manager.runtime.native.arguments import resume_id
    from agent_manager.runtime.continuation.registry import for_manager
    from agent_manager.runtime.native.configuration import apply as apply_native_config
    if manager.provider == 'codex': conversation_id = conversation_id or resume_id(args)
    context = defaults(manager)
    previous = ThreadStore(manager).read(agent, conversation_id) if conversation_id else None
    if previous:
        context = context.resolve(**{key: previous[key] for key in ('labels', 'metadata')})
    context = context.resolve(labels=labels, metadata=metadata,
        inherit_labels=inherit_labels, inherit_metadata=inherit_metadata)
    use_undercurrent = manager.config['runtime'] == 'undercurrent'
    if (context.labels or context.metadata) and not use_undercurrent:
        raise Error('Launch labels and metadata require runtime = "undercurrent" for CLI launches')
    if use_undercurrent:
        from agent_manager.interfaces.cli.terminal import preflight, launch, dependency
        preflight()
        if context.metadata: require_metadata(dependency().Client, context.metadata)
    run_id = uuid.uuid4().hex
    with locks(manager.lock_root, 'registry', wait=True):
        state = manager.state()
        _home_plan.claim(manager, state, agent)
        if validate is not None: validate()
        state['runs'][run_id] = dict(agent=agent, pid=os.getpid(), **_home_plan.provenance,
            profile_id=agent.profile_id, environment_id=agent.environment_id,
            home=str(manager.home(agent).resolve()), thread_id=conversation_id or _native_session_id,
            cwd=str(manager.working_directory()),
            labels=attributes({**context.labels, 'agent_manager.run_id': run_id,
                               'agent_manager.profile': agent}, 'labels', internal=True),
            metadata=context.metadata)
        manager.save(state)
    _home_plan.release()
    exit_code = None
    try:
        for_manager(manager).acquire_session(manager, agent, run_id,
            conversation_id or _native_session_id, _home_plan.source_session)
        def invoke(arguments):
            nonlocal exit_code
            if not use_undercurrent and manager.provider == 'codex':
                from agent_manager.runtime.native.launch import terminal_launch
                arguments, _ = terminal_launch(manager, arguments, {}, managed=True)
            exit_code = launch(manager, run_id, arguments) if use_undercurrent else manager._invoke_native(manager.home(agent), arguments)
            native_id = conversation_id or _native_session_id
            if native_id and exit_code == 0:
                from agent_manager.profiles.sessions import remember
                source = _home_plan.source_session
                remember(manager, agent, native_id, **({
                    'history_home': source['history_home'], 'cwd': source['cwd']} if source else {'cwd': str(manager.working_directory())}))
            return exit_code
        return invoke(apply_native_config(manager, manager.launch_args(agent, args, model), native_config))
    finally:
        if manager.state().get('runs', {}).get(run_id, {}).get('runtime') != 'undercurrent':
            from agent_manager.runtime.session_leases import release
            release(manager, run_id)
        with locks(manager.lock_root, 'registry', wait=True):
            state = manager.state()
            record = state['runs'].get(run_id)
            if record is not None and record.get('runtime') != 'undercurrent':
                del state['runs'][run_id]
                record.update(status='finished' if exit_code == 0 else 'failed', exit_code=exit_code)
                atomic(manager.root / 'sdk-runs' / (run_id + '.json'), json.dumps(record).encode())
                manager.save(state)
