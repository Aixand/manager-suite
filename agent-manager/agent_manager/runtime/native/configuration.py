"""Explicit, per-launch native settings; no resource resolution or persistence."""
from copy import deepcopy
import json
import math
import tomllib

from agent_manager.errors import Error
from agent_manager.runtime.native.arguments import command

ALLOWED_ROOTS = frozenset(('mcp_servers', 'hooks', 'features', 'developer_instructions',
    'model', 'model_reasoning_effort', 'model_reasoning_summary', 'model_verbosity',
    'approval_policy', 'sandbox_mode', 'sandbox_workspace_write', 'tools', 'web_search'))

def normalize(manager, value):
    if value is None: return {}
    if not isinstance(value, dict): raise ValueError('native_config must be a mapping')
    if value and manager.provider != 'codex':
        raise Error('Native launch configuration is currently qualified for Codex only')
    def check(item):
        if isinstance(item, (str, bool, int)):
            if isinstance(item, str) and '\0' in item: raise ValueError('Native settings cannot contain NUL')
        elif isinstance(item, float) and math.isfinite(item): pass
        elif isinstance(item, list):
            for child in item: check(child)
        elif isinstance(item, dict) and all(isinstance(key, str) and '\0' not in key for key in item):
            for child in item.values(): check(child)
        else: raise ValueError('Native settings must contain finite TOML-compatible values')
    paths = []
    for key, item in value.items():
        if not isinstance(key, str) or not key or any(c in key for c in '\r\n\0='):
            raise ValueError('Native configuration keys must be TOML paths')
        try:
            parsed = tomllib.loads(key + '=0')
            if not parsed: raise tomllib.TOMLDecodeError('Empty key', key, 0)
        except tomllib.TOMLDecodeError: raise ValueError('Native configuration keys must be TOML paths') from None
        check(item)
        if next(iter(parsed)) not in ALLOWED_ROOTS:
            raise Error('Native launch setting is not qualified; storage, identity and authentication remain profile-owned')
        node, path = parsed, []
        while isinstance(node, dict):
            part, node = next(iter(node.items()))
            path.append(part)
        path = tuple(path)
        if any(path[:len(prior)] == prior or prior[:len(path)] == path for prior in paths):
            raise ValueError('Native configuration paths must not overlap or repeat')
        paths.append(path)
    return deepcopy(value)


def toml(value):
    if isinstance(value, dict):
        return '{' + ','.join(toml(key) + '=' + toml(child) for key, child in value.items()) + '}'
    if isinstance(value, list): return '[' + ','.join(toml(child) for child in value) + ']'
    return json.dumps(value, ensure_ascii=False, allow_nan=False).replace('\x7f', '\\u007f')


def apply(manager, arguments, settings):
    settings = normalize(manager, settings)
    overrides = [token for key, value in sorted(settings.items()) for token in ('-c', key + '=' + toml(value))]
    arguments = list(arguments)
    position = len(arguments) - len(command(arguments)) if overrides else 0
    if '--' in arguments: position = min(position, arguments.index('--'))
    return arguments[:position] + overrides + arguments[position:]
