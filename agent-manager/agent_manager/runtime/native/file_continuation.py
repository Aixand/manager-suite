"""Managed handoff of native file sessions without changing account homes.

Claude resumes the original transcript path. Copilot follows one journaled session
link. Registry leases coordinate Manager runs; standalone vendor writers must be
closed by their owner. Neither vendor supplies a lifetime lock for this handoff.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from agent_manager.errors import Error
from agent_manager.providers import terminal_policy
from agent_manager.runtime.continuation.coordination import check_idle
from agent_manager.storage.files import private_dir, atomic
from agent_manager.storage.locks import locks
from agent_manager.storage.platform import check_process

VERSIONS = {provider: terminal_policy.recommended(provider) for provider in ('claude', 'copilot')}


def available():
    return sys.platform == 'linux'


@lru_cache(maxsize=16)
def _version(provider, program, modified, size):
    with tempfile.TemporaryDirectory(prefix='agent-session-version-') as directory:
        variables = {key: os.environ[key] for key in ('PATH', 'LANG', 'SYSTEMROOT') if key in os.environ}
        variables.update(HOME=directory, USERPROFILE=directory, TMPDIR=directory,
            CLAUDE_CONFIG_DIR=directory, COPILOT_HOME=directory,
            DISABLE_AUTOUPDATER='1', CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1',
            COPILOT_AUTO_UPDATE='false', COPILOT_OFFLINE='true')
        try:
            result = subprocess.run([program, '--version'], env=variables, cwd=directory,
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            return False
        from agent_manager.providers.policy import cli_version
        return result.returncode == 0 and terminal_policy.supports_continuation(provider, cli_version(result.stdout))


def require_version(manager):
    if not available(): raise Error('Claude and Copilot cross-environment continuation currently requires Linux')
    program = shutil.which(manager.config['executable'])
    if program is None: raise Error('The native provider CLI is unavailable')
    program = Path(program).resolve(); stat = program.stat()
    if not _version(manager.provider, str(program), stat.st_mtime_ns, stat.st_size):
        raise Error(terminal_policy.version_error(manager.provider))


def check_claude_writers(manager, state, source, target_home):
    """Reject native PID markers we can observe; this cannot exclude future bypasses."""
    if manager.provider != 'claude': return
    homes = {Path(row['home']) for row in state['environments'].values()} | {Path(source['history_home']), Path(target_home)}
    for home in homes:
        for marker in (home / 'sessions').glob('[0-9]*.json'):
            if not marker.stem.isdigit(): continue
            try: check_process(int(marker.stem))
            except ProcessLookupError: continue
            except PermissionError: pass
            try:
                if marker.stat().st_size > 65536: raise ValueError()
                row = json.loads(marker.read_text())
                if row.get('sessionId') != source['native_id']: continue
            except (OSError, ValueError, AttributeError):
                raise Error('A live Claude session marker could not be verified; close standalone Claude sessions before handing off history') from None
            raise Error('The original conversation is open in a native Claude process; close it before continuing')


@dataclass(frozen=True)
class HistoryView:
    provider: str
    home: Path
    source: Path
    history_home: Path
    native_id: str
    source_ref: str
    environment_id: str
    session: dict

    @property
    def receipt(self):
        return dict(schema=1, provider=self.provider, home=str(self.home), source=str(self.source),
            history_home=str(self.history_home), native_id=self.native_id, source_ref=self.source_ref,
            environment_id=self.environment_id, session=dict(self.session))

    def validate(self, manager):
        if self.provider == 'claude':
            from agent_manager.runtime.native.claude_sessions import transcript
            if transcript(self.history_home, self.native_id) != self.source:
                raise Error('The original Claude transcript changed location')
        else:
            from agent_manager.runtime.native.copilot_sessions import event_log
            if event_log(self.history_home, self.native_id).parent != self.source:
                raise Error('The original Copilot session changed location')
            parent = self.home / 'session-state'
            link = parent / self.native_id
            if parent.is_symlink() or parent.resolve() != parent or not link.is_symlink() or link.resolve() != self.source:
                raise Error('The managed Copilot session link changed; continuation was not started')
        state = manager.state()
        from agent_manager.profiles.sessions import check_reference
        check_reference(manager, self.session, state=state)
        row = state['environments'].get(self.environment_id)
        if row is None or Path(row['home']).resolve() != self.home:
            raise Error('The destination environment home changed')
        if state.get('session_routes', {}).get(self.environment_id, {}).get(self.native_id) != self.source_ref:
            raise Error('The destination session route changed; continuation was not started')

    def command(self, manager, arguments):
        self.validate(manager)
        if self.provider == 'claude':
            from agent_manager.runtime.native.claude_sessions import validate_arguments
            index, inline = validate_arguments(arguments, self.native_id)
            arguments = list(arguments)
            if inline: arguments[index] = arguments[index].split('=', 1)[0] + '=' + str(self.source)
            else: arguments[index + 1] = str(self.source)
            return self.wrapped(manager, arguments)
        from agent_manager.runtime.native.copilot_sessions import validate_arguments
        validate_arguments(arguments, self.native_id)
        return self.wrapped(manager, list(arguments))

    def wrapped(self, manager, arguments):
        import hashlib
        payload = dict(self.receipt, state_root=str(manager.root.parent), config=str(manager.config_path))
        data = json.dumps(payload, sort_keys=True).encode()
        path = manager.root / 'native-continuations' / (hashlib.sha256(data).hexdigest() + '.json')
        if not path.exists(): atomic(path, data)
        return [sys.executable, '-m', 'agent_manager.runtime.native.file_continue', str(path), '--', *arguments]


def prepare(manager, profile, source, *, args, cwd):
    from agent_manager.profiles.sessions import check_reference, validate_source
    from agent_manager.runtime.native import claude_sessions, copilot_sessions
    target = manager.resolve_profile(profile); home = manager.home(target).resolve()
    history = Path(source['history_home']).resolve()
    if home.is_relative_to(history) or history.is_relative_to(home):
        raise Error('Cross-environment continuation requires separate, non-nested native homes')
    validator = claude_sessions if manager.provider == 'claude' else copilot_sessions
    validator.validate_arguments([manager.config['executable'], *args], source['native_id'])
    validate_source(manager, source, target)
    require_version(manager)
    path = claude_sessions.transcript(history, source['native_id']) if manager.provider == 'claude' else copilot_sessions.event_log(history, source['native_id']).parent
    view = HistoryView(manager.provider, home, path, history, source['native_id'], source['ref'], target.environment_id, dict(source))
    with locks(manager.lock_root, 'registry', wait=True):
        state = manager.state(); check_reference(manager, source, state=state)
        check_idle(manager, state, source); check_claude_writers(manager, state, source, home)
        routes = state.setdefault('session_routes', {}).setdefault(target.environment_id, {})
        previous = routes.get(source['native_id'])
        if previous is not None and previous != source['ref']:
            raise Error('This native session ID already routes to another history store in the destination environment')
        # A distinct local conversation with the same UUID must never be hidden.
        for saved in state['sessions'].values():
            if saved['native_id'] == source['native_id'] and Path(saved['history_home']).resolve() == home:
                raise Error('The destination already owns another conversation with this native session ID')
        link = None
        if manager.provider == 'copilot':
            parent = home / 'session-state'; link = parent / source['native_id']
            if parent.is_symlink() or parent.resolve() != parent:
                raise Error('The destination session directory must not be a symlink')
            if os.path.lexists(link) and (previous is None or not link.is_symlink() or link.resolve() != path):
                raise Error('The destination session path is occupied; no native files were replaced')
        else:
            # UUID-based native pickers must not ambiguously select a local copy.
            if any((home / 'projects').glob('*/' + source['native_id'] + '.jsonl')):
                raise Error('The destination already contains a Claude transcript with this session ID')
        # Journal ownership before publishing a link. An interrupted preparation
        # can safely recreate its missing link; an untracked link is never adopted.
        routes[source['native_id']] = source['ref']; manager.save(state)
        if link is not None and not os.path.lexists(link):
            private_dir(link.parent)
            try: link.symlink_to(path, target_is_directory=True)
            except OSError as error:
                raise Error('Could not create the Copilot session link; native history was preserved') from error
    view.validate(manager)
    return view


def validate_receipt(manager, receipt, *, run_id=None):
    """Revalidate retained commands immediately before runtime submission."""
    if receipt.get('schema') != 1 or receipt.get('provider') != manager.provider:
        raise Error('Invalid native continuation receipt')
    view = HistoryView(receipt['provider'], Path(receipt['home']), Path(receipt['source']),
        Path(receipt['history_home']), receipt['native_id'], receipt['source_ref'], receipt['environment_id'], receipt['session'])
    view.validate(manager)
    state = manager.state()
    source = state['sessions'].get(view.source_ref)
    if source is None or source['native_id'] != view.native_id or Path(source['history_home']).resolve() != view.history_home:
        raise Error('The original native session reference changed')
    check_idle(manager, state, source, exclude_run=run_id)
    check_claude_writers(manager, state, source, view.home)


def requested_identity(arguments, provider):
    """Return (resume UUID, fresh UUID, native picker) from public CLI argv."""
    import uuid
    resume, fresh, picker = None, None, False
    if provider == 'claude':
        from agent_manager.runtime.native.claude_sessions import VALUE_OPTIONS
    else:
        from agent_manager.runtime.native.copilot_sessions import VALUE_OPTIONS
    index = 0
    arguments = list(arguments)
    while index < len(arguments):
        key, equals, inline = arguments[index].partition('=')
        if key in ('--resume', '-r', '--session-id'):
            value = inline if equals else arguments[index + 1] if index + 1 < len(arguments) else None
            if key != '--session-id' and (value is None or value.startswith('-')):
                if resume or fresh or picker: raise Error('Choose one native session identity')
                picker = True; index += 1; continue
            try:
                identifier = str(uuid.UUID(value))
                if identifier != value: raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise Error('Use an exact native session UUID or agent PROVIDER continue SESSION_REFERENCE') from None
            if resume or fresh or picker: raise Error('Choose one native session identity')
            if key == '--session-id': fresh = identifier
            else: resume = identifier
            index += 1 if equals else 2
        elif key in ('--continue', '-c'):
            if resume or fresh or picker: raise Error('Choose one native session identity')
            picker = True; index += 1
        else: index += 2 if key in VALUE_OPTIONS and not equals else 1
    return resume, fresh, picker


def acquire_session(manager, run_id, native_id, home):
    from agent_manager.runtime.session_leases import acquire
    acquire(manager, run_id, home, scope=manager.provider + ' native session ' + native_id, exclusive=True)


def check_new_identity(manager, profile, native_id):
    """Fresh launches cannot overwrite a retained or linked conversation."""
    target = manager.resolve_profile(profile); state = manager.state()
    home = manager.home(target).resolve()
    routed = native_id in state.get('session_routes', {}).get(target.environment_id, {})
    retained = any(row['native_id'] == native_id and Path(row['history_home']).resolve() == home
                   for row in state['sessions'].values())
    exists = any((home / 'projects').glob('*/' + native_id + '.jsonl')) if manager.provider == 'claude' else os.path.lexists(home / 'session-state' / native_id)
    if routed or retained or exists:
        raise Error('This native session ID already belongs to a conversation; use continue instead of creating a new session')
