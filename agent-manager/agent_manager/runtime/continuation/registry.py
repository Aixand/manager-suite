"""Built-in mechanisms; manifests select implementations, never import paths."""
from dataclasses import dataclass
from agent_manager.errors import Error
from agent_manager.runtime.continuation.base import NativeAdapter
from agent_manager.runtime.continuation.codex import CodexAdapter
from agent_manager.runtime.continuation.files import ClaudeAdapter, CopilotAdapter
from agent_manager.runtime.continuation.opencode import OpenCodeAdapter


@dataclass(frozen=True)
class Registration:
    provider: str
    adapter: NativeAdapter
    home_strategy: str
    home_environment: str | None
    session_strategy: str
    resume_args: tuple[str, ...]


_NATIVE = NativeAdapter()
_REGISTERED = (
    Registration('codex', CodexAdapter(), 'environment-directory', 'CODEX_HOME', 'codex-transcripts', ('resume',)),
    Registration('claude', ClaudeAdapter(), 'environment-directory', 'CLAUDE_CONFIG_DIR', 'native-picker', ('--resume',)),
    Registration('copilot', CopilotAdapter(), 'environment-directory', 'COPILOT_HOME', 'native-picker', ('--resume',)),
    Registration('opencode', OpenCodeAdapter(), 'opencode-xdg', 'XDG_DATA_HOME', 'opencode-database', ('--session',)),
)
_PROVIDERS = {entry.provider: entry.adapter for entry in _REGISTERED}
_MECHANISMS = {entry.adapter.mechanism: entry for entry in _REGISTERED}
_STORAGE = {entry.session_strategy: entry.adapter for entry in _REGISTERED
            if entry.session_strategy in ('codex-transcripts', 'opencode-database')}


def mechanisms(): return tuple(_MECHANISMS)


def storage_adapter(strategy): return _STORAGE.get(strategy)


def definition_error(provider, terminal):
    mechanism = terminal.get('sessions', {}).get('cross_environment_resume')
    if not mechanism or mechanism == 'disabled': return None
    entry = _MECHANISMS.get(mechanism)
    if entry is None: return 'requires a registered native continuation mechanism'
    if (provider != entry.provider or terminal['home']['strategy'] != entry.home_strategy
            or terminal['home'].get('environment') != entry.home_environment
            or terminal['sessions']['strategy'] != entry.session_strategy
            or tuple(terminal['sessions']['resume_args']) != entry.resume_args):
        return 'requires the matching built-in native home and session strategy'


def for_provider(provider, mechanism=None):
    if mechanism and mechanism != 'disabled':
        entry = _MECHANISMS.get(mechanism)
        if entry is None or entry.provider != provider:
            raise Error('Continuation mechanism does not match its built-in provider')
        return entry.adapter
    return _PROVIDERS.get(provider, _NATIVE)


def for_manager(manager):
    return for_provider(manager.provider, manager.provider_spec.sessions.cross_environment_resume_mechanism)


def availability(provider, mechanism, disabled_reason=None):
    adapter = for_provider(provider, mechanism)
    if not mechanism or mechanism == 'disabled':
        return False, disabled_reason or _NATIVE.availability()[1], None
    enabled, reason = adapter.availability()
    return enabled, reason, adapter.history_mode if enabled else None
