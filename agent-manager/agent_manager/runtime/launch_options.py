"""Pure SDK launch validation, before selection or native history preparation."""
from collections.abc import Mapping
import math
import os
import uuid

from agent_manager.errors import Error
from agent_manager.providers.arguments import model_id
from agent_manager.runtime.context import attributes
from agent_manager.runtime.native.configuration import normalize


def validate(manager, options, *, continuation=False):
    result = dict(options)
    args = result.get('args', ())
    if isinstance(args, (str, bytes)): raise ValueError('args must be a sequence of strings')
    args = tuple(args)
    if not all(isinstance(item, str) for item in args): raise ValueError('args must be a sequence of strings')
    if continuation and (args or any(result.get(key) is not None for key in ('thread_id', 'resume_id'))):
        raise Error('Continuation supplies its own native identity; forwarded args and resume selectors are unavailable')
    result['args'] = args
    timeout = result.get('timeout', 60.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    if result.get('model') is not None: model_id(result['model'])
    if result.get('cwd') is not None:
        try: directory = os.fspath(result['cwd'])
        except TypeError: raise ValueError('cwd must be a path') from None
        if not isinstance(directory, str) or '\0' in directory: raise ValueError('cwd must be a text path without NUL')
    if result.get('env') is not None:
        env = result['env']
        if not isinstance(env, Mapping) or any(not isinstance(key, str) or not key or '=' in key or '\0' in key
                or not isinstance(value, str) or '\0' in value for key, value in env.items()):
            raise ValueError('env must map valid environment names to strings without NUL')
        result['env'] = dict(env)
    for key in ('detection', 'inherit_labels', 'inherit_metadata'):
        if key in result and not isinstance(result[key], bool): raise ValueError(key + ' must be a boolean')
    if result.get('detection_profile') is not None:
        value = result['detection_profile']
        if not isinstance(value, str) or not value.strip() or '\0' in value:
            raise ValueError('detection_profile must be a nonempty string')
    for key in ('labels', 'metadata'):
        if result.get(key) is not None: result[key] = attributes(result[key], key)
    if result.get('idempotency_key') is not None:
        try: uuid.UUID(result['idempotency_key'])
        except (ValueError, TypeError, AttributeError): raise ValueError('idempotency_key must be a UUID') from None
    result['native_config'] = normalize(manager, result.get('native_config'))
    return result
