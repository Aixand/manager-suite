"""Literal native launch arguments, scoped to a provider or native environment."""
import copy
import json
import tomllib

from agent_manager.errors import Error
from agent_manager.profiles.identities import environment_id
from agent_manager.storage.files import atomic
from agent_manager.storage.locks import locks


def validate(values):
    if not isinstance(values, list) or any(not isinstance(value, str) or '\0' in value for value in values):
        raise Error('Saved arguments must be a list of strings without NUL characters')
    return list(values)


def effective(manager, profile):
    state = manager.state()
    return (list(manager.config.get('run_args', [])) + list(manager.config.get('launch_args', []))
        + validate(state.get('launch_args', {}).get(environment_id(state, profile), [])))


def changed(current, action, values):
    if action == 'set': return values
    if action == 'add': return current + values
    if action == 'clear': return []
    if action == 'list': return current
    # Remove a whole contiguous argv sequence, never an option's value alone by
    # accident. All exact occurrences are removed, with remaining order intact.
    result, index = [], 0
    while index < len(current):
        if current[index:index + len(values)] == values:
            index += len(values)
        else:
            result.append(current[index]); index += 1
    return result


def snapshot(manager, values, profile=None):
    from agent_manager.storage.json import digest
    target = manager.resolve_profile(profile) if profile is not None else None
    return dict(provider=manager.provider, profile=str(target) if target is not None else None,
        environment_id=target.environment_id if target is not None else None, args=values,
        revision=digest([manager.provider, target.environment_id if target is not None else str(manager.configuration_source.resolve()), values]))


def configure(manager, action, values=(), *, profile=None, expected_revision=None):
    if not isinstance(values, (list, tuple)):
        raise Error('Saved arguments must be an array of literal strings')
    values = validate(list(values))
    if action not in ('add', 'remove', 'clear', 'list', 'set'): raise Error('Unknown argument operation')
    if action in ('add', 'remove') and not values:
        raise Error(f'Supply arguments after --, for example: agent {manager.provider} config args {action} -- --yolo')
    if action in ('list', 'clear') and values: raise Error(f'{action} does not accept provider arguments')
    if action == 'list':
        state = manager.state()
        current = (validate(state.get('launch_args', {}).get(environment_id(state, profile), []))
            if profile is not None else list(manager.config.get('launch_args', [])))
        return snapshot(manager, current, profile)
    if profile is not None:
        target = manager.resolve_profile(profile)
        with locks(manager.lock_root, 'registry', wait=True):
            state = manager.state()
            identifier = environment_id(state, target)
            current = validate(state.get('launch_args', {}).get(identifier, []))
            if expected_revision is not None and snapshot(manager, current, target)['revision'] != expected_revision:
                raise Error('Saved arguments changed; refresh before editing')
            result = changed(current, action, values)
            if action != 'list':
                if result: state.setdefault('launch_args', {})[identifier] = result
                else: state.get('launch_args', {}).pop(identifier, None)
                manager.save(state)
        return snapshot(manager, result, target)
    path = manager.configuration_source.resolve()
    with locks(path.parent / '.locks', 'provider-config', wait=True):
        text = path.read_text(encoding='utf-8') if path.exists() else ''
        document = tomllib.loads(text)
        from agent_manager.providers.config import load_registry
        registry = load_registry(manager.configuration_source, allow_missing=True)
        providers = document.get('providers', {})
        matching = [key for key in providers if registry.names.get(key, key) == manager.provider]
        current = list(registry.resolve(manager.provider).settings.get('launch_args', []))
        if expected_revision is not None and snapshot(manager, current)['revision'] != expected_revision:
            raise Error('Saved arguments changed; refresh before editing')
        result = changed(current, action, values)
        if action != 'list':
            expected = copy.deepcopy(document)
            tables = expected.setdefault('providers', {})
            # Keep any alias tables, but put the one launch_args override in the
            # final matching table so it wins in the existing merge order.
            for key in matching: tables[key].pop('launch_args', None)
            key = matching[-1] if matching else manager.provider
            tables.setdefault(key, {})['launch_args'] = result
            atomic(path, _rewrite(text, expected, matching, key, result))
            from dataclasses import replace
            with manager._configuration_lock:
                settings = {**manager._config, 'launch_args': list(result)}
                manager.config = settings
                manager.provider_spec = replace(manager._provider_spec, settings=settings)
                manager.config_path = manager.configuration_source
    return snapshot(manager, result)


def _rewrite(text, expected, matching, key, values):
    """Preserve untouched TOML text for ordinary tables; validate every rewrite."""
    from agent_manager.storage.toml import _toml_header_path, _toml_document
    statement = 'launch_args = ' + json.dumps(values, ensure_ascii=False) + '\n'
    output, section, inserted = [], None, False
    for line in text.splitlines(keepends=True):
        header = _toml_header_path(line)
        if header is not None:
            section = header
            if header == ('providers', key):
                output.extend([line.rstrip('\r\n') + '\n', statement])
                inserted = True
                continue
        if section in [('providers', name) for name in matching]:
            try:
                if set(tomllib.loads(line)) == {'launch_args'}: continue
            except tomllib.TOMLDecodeError: pass
        output.append(line)
    candidate = ''.join(output)
    if not inserted:
        candidate = candidate.rstrip() + '\n\n[providers.' + json.dumps(key) + ']\n' + statement
    try:
        if tomllib.loads(candidate) == expected: return candidate.encode()
    except tomllib.TOMLDecodeError: pass
    # Multiline or inline tables use the existing loss-checked serializer.
    return _toml_document(expected)
