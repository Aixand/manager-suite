"""Noninteractive automatic startup with a durable, keyed selection decision."""
from pathlib import Path
import inspect
import tomllib
import uuid

from agent_manager.errors import Error
from agent_manager.interfaces.contracts import SelectionDecision
from agent_manager.runtime.runs import read_run
from agent_manager.runtime.models import RunNotFoundError
from agent_manager.storage.files import atomic
from agent_manager.storage.json import canonical, digest, read
from agent_manager.storage.locks import locks


def options_for(client, options, *, continuation=False):
    """Validate before budget probes or native history preparation."""
    if any(key.startswith('_') or key == 'profile' for key in options):
        raise Error('Automatic execution does not accept explicit profiles or private options')
    try: inspect.signature(client.spawn).bind_partial(**options)
    except TypeError as error: raise Error(str(error)) from None
    if not continuation and any(options.get(key) is not None for key in ('thread_id', 'resume_id')):
        raise Error('Use continue_session(auto=True) for automatic native continuation')
    from agent_manager.runtime.launch_options import validate
    result = validate(client.manager, options, continuation=continuation)
    if result.get('cwd') is not None:
        result['cwd'] = str(Path(result['cwd']).expanduser().resolve())
    for key in list(result['native_config']):
        parsed = tomllib.loads(key + '=0')
        if 'model' in parsed:
            if isinstance(parsed['model'], dict): raise Error('Automatic selection requires a scalar native model setting')
            if key != 'model': result['native_config']['model'] = result['native_config'].pop(key)
    native_model = result['native_config'].get('model')
    for value in (result.get('model'), native_model):
        if value is not None and (not isinstance(value, str) or not value or '\0' in value):
            raise ValueError('model must be a nonempty string')
    if native_model is not None and result.get('model') is not None and native_model != result['model']:
        raise Error('Automatic selection requires consistent model and native_config.model settings')
    return result


def _intent(client, options, kind, session, fallback):
    values = {name: parameter.default for name, parameter in inspect.signature(client.spawn).parameters.items()
        if not name.startswith('_') and name not in ('profile', 'idempotency_key', 'timeout')}
    values.update({key: value for key, value in options.items() if key != 'timeout'})
    values['args'] = list(values.get('args', ()))
    values['env'] = dict(values.get('env') or {})
    cwd = values.get('cwd')
    values['cwd'] = str(Path(cwd or Path.cwd()).expanduser().resolve()) if kind == 'start' or cwd is not None else None
    context = client._context.resolve(**{key: values.pop(key) for key in
        ('labels', 'metadata', 'inherit_labels', 'inherit_metadata')})
    return digest(dict(kind=kind, session=session, fallback=fallback, options=values,
        context=context.as_dict(), runtime_home=str(Path(client.runtime.connection_options['home']).expanduser().resolve())))


def plan(client, options, *, kind='start', session=None, fallback='error', idempotency_key=None):
    if fallback not in ('error', 'default'): raise ValueError('fallback must be error or default')
    if session is not None and (not isinstance(session, str) or not session):
        raise ValueError('session must be a native session ID or Manager session reference')
    try: key = uuid.UUID(idempotency_key).hex if idempotency_key is not None else uuid.uuid4().hex
    except (ValueError, TypeError, AttributeError): raise ValueError('idempotency_key must be a UUID') from None
    intent = _intent(client, options, kind, session, fallback)
    manager = client.manager
    path = manager.root / 'automatic-starts' / (key + '.json')
    with locks(manager.lock_root, 'automatic-' + key, wait=True):
        saved = read(path) if path.exists() else None
        if saved is not None and (not isinstance(saved, dict) or saved.get('schema') != 'agent-manager/automatic-start-1'):
            raise Error('Unsupported automatic startup journal')
        if saved is not None and saved.get('intent') != intent:
            raise Error('Idempotency key was reused with different automatic launch arguments')
        try: record = read_run(manager, key)
        except RunNotFoundError: record = None
        if record is not None:
            if saved is None or record.get('automatic_intent') != intent:
                raise Error('Idempotency key already names a different managed launch')
            return SelectionDecision.from_dict(saved.get('selection')), key, intent, True
        if saved is not None:
            return SelectionDecision.from_dict(saved.get('selection')), key, intent, False
        # Accepted runs are recovered above, before checking launch-only paths.
        if options.get('cwd') is not None and not Path(options['cwd']).is_dir():
            raise Error('Working directory does not exist')
        allowed = None
        if session is not None:
            from agent_manager.runtime.continuation.coordinator import resolve_request
            from agent_manager.profiles.sessions import execution_profiles
            source = resolve_request(manager, session).source
            if options.get('cwd') is not None and Path(options['cwd']).expanduser().resolve() != Path(source['cwd']).resolve():
                raise Error('Continuation requires its captured working directory')
            state = manager.state()
            allowed = {state['profiles'][name]['environment_id'] for name in execution_profiles(manager, source)}
        from agent_manager.profiles.selection import choose, Decision, SelectionUnavailable
        from agent_manager.environments.operations import revision
        model = options.get('model') or options['native_config'].get('model')
        # Budget/model readers must use this launch's workspace, not the caller's.
        from agent_manager.runtime.execution import using_directory
        directory = source['cwd'] if session is not None else options.get('cwd') or Path.cwd()
        with using_directory(directory):
            try: decision = choose(manager, model=model, args=options['args'], environment_ids=allowed)
            except SelectionUnavailable:
                if fallback != 'default': raise
                target = manager.resolve_profile()
                if allowed is not None and target.environment_id not in allowed:
                    raise Error('The default profile cannot continue the selected native session') from None
                decision = Decision(target, revision(manager.state(), target.environment_id),
                    'Configured default profile (explicit fallback without budget qualification)', model)
        selected = SelectionDecision.capture(manager, decision)
        atomic(path, canonical(dict(schema='agent-manager/automatic-start-1', intent=intent,
            selection=selected.to_dict())))
        return selected, key, intent, False
