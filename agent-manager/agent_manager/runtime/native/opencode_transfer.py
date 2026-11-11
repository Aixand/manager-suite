"""Native OpenCode transcript transfer. No credential or SQLite writes by Manager."""
import copy
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from agent_manager.errors import Error
from agent_manager.providers import terminal_policy

VERSION = terminal_policy.requirements('opencode')['recommendedVersion']
MAX_EXPORT = 64 * 1024 * 1024
IDENTIFIER = re.compile(r'ses_[A-Za-z0-9]+\Z')


def _program(manager):
    program = shutil.which(manager.config['executable'])
    if program is None: raise Error('The native OpenCode CLI is unavailable')
    return Path(program).resolve()


def _command(program, args):
    if os.name == 'nt' and program.suffix.lower() in ('.cmd', '.ps1'):
        from agent_manager.providers.installation.engine import executable_command
        return executable_command(str(program), list(args))
    return [str(program), *args]


def _environment(manager, home, cwd):
    # Only native home configuration supplies provider credentials to these
    # metadata operations. Never forward an inherited server or database route.
    variables = {key: os.environ[key] for key in ('PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT',
        'LANG', 'LC_ALL', 'TMPDIR', 'TEMP', 'TMP') if key in os.environ}
    variables.update(manager.environment(Path(home), cwd=cwd))
    variables.update(OPENCODE_DISABLE_AUTOUPDATE='true', OPENCODE_DISABLE_MODELS_FETCH='true')
    return variables


@lru_cache(maxsize=8)
def _version(program, modified, size):
    from agent_manager.providers.policy import cli_version
    with tempfile.TemporaryDirectory(prefix='agent-opencode-version-') as directory:
        variables = {key: os.environ[key] for key in ('PATH', 'LANG', 'SYSTEMROOT') if key in os.environ}
        variables.update(HOME=directory, USERPROFILE=directory, XDG_CONFIG_HOME=directory,
            XDG_DATA_HOME=directory, XDG_STATE_HOME=directory, XDG_CACHE_HOME=directory,
            OPENCODE_CONFIG_DIR=directory, OPENCODE_DB=str(Path(directory) / 'opencode.db'))
        try:
            result = subprocess.run(_command(Path(program), ['--version']), env=variables,
                cwd=directory, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
            return result.returncode == 0 and terminal_policy.supports_continuation('opencode', cli_version(result.stdout))
        except (OSError, subprocess.TimeoutExpired, UnicodeError): return False


def require_version(manager):
    program = _program(manager); stat = program.stat()
    if not _version(str(program), stat.st_mtime_ns, stat.st_size):
        raise Error(terminal_policy.version_error('opencode'))


def _invoke(manager, home, cwd, args):
    from agent_manager.providers.opencode import database
    path = database(manager, Path(home))
    if path.resolve() != path:
        raise Error('OpenCode session transfer requires an unredirected native database')
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(_command(_program(manager), args), cwd=cwd,
                env=_environment(manager, home, cwd), stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.DEVNULL, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            raise Error('OpenCode native session transfer did not complete; retry using the retained session reference') from None
        if result.returncode:
            raise Error('OpenCode native session command failed; retry the retained reference to reconcile a possible import')
        size = output.tell()
        if size > MAX_EXPORT: raise Error('The native session export exceeds the supported transfer size')
        output.seek(0)
        return output.read()


def export_session(manager, home, native_id, cwd):
    if not isinstance(native_id, str) or not IDENTIFIER.fullmatch(native_id):
        raise Error('OpenCode continuation requires an exact native session ID')
    from agent_manager.providers.opencode import database
    if not database(manager, Path(home)).is_file():
        raise Error('The native OpenCode history database is unavailable')
    try:
        payload = json.loads(_invoke(manager, home, cwd, ['session', 'export', native_id, '--standalone']))
    except (ValueError, UnicodeError):
        raise Error('Invalid native OpenCode session export') from None
    validate_payload(payload, native_id)
    return payload


def validate_payload(payload, native_id):
    if (not isinstance(payload, dict) or set(payload) != {'info', 'messages'}
            or not isinstance(payload['info'], dict) or payload['info'].get('id') != native_id
            or not isinstance(payload['messages'], list)
            or not isinstance(payload['info'].get('location'), dict)
            or not isinstance(payload['info']['location'].get('directory'), str)
            or not Path(payload['info']['location']['directory']).is_absolute()):
        raise Error('Invalid native OpenCode session export')
    identifiers = set()
    for message in payload['messages']:
        if (not isinstance(message, dict) or not isinstance(message.get('id'), str)
                or not message['id'].startswith('msg_') or message['id'] in identifiers):
            raise Error('Invalid native OpenCode message identity')
        identifiers.add(message['id'])
    # Native import does not reconstruct a staged revert or another parent's
    # execution state. Refuse to silently turn either into a different context.
    if payload['info'].get('parentID') or payload['info'].get('revert'):
        raise Error('Native OpenCode transfer requires a root session without a staged revert')


def _references(value, identities):
    """Rewrite structural message references, never arbitrary text or metadata."""
    if isinstance(value, list): return [_references(item, identities) for item in value]
    if not isinstance(value, dict): return value
    result = {}
    for key, item in value.items():
        if key in ('recent', 'messageID', 'messageId') and isinstance(item, str):
            result[key] = identities.get(item, item)
        elif key in ('text', 'metadata', 'providerState', 'providerResultState', 'input', 'output', 'content'):
            result[key] = item
        else:
            result[key] = _references(item, identities)
    return result


def revision(payload):
    """Content identity survives native IDs, import timestamps and UI visits."""
    identities = {message['id']: 'message:' + str(index) for index, message in enumerate(payload['messages'])}
    messages = [dict(_references(message, identities), id=identities[message['id']]) for message in payload['messages']]
    # These are the semantic session fields retained by the pinned native import.
    info = {key: payload['info'][key] for key in ('title', 'agent', 'model', 'metadata', 'permissions', 'cost', 'tokens')
            if key in payload['info']}
    info['directory'] = payload['info'].get('location', {}).get('directory')
    data = json.dumps(dict(info=info, messages=messages), sort_keys=True, separators=(',', ':'), allow_nan=False)
    return 'sha256:' + hashlib.sha256(data.encode()).hexdigest()


def for_import(payload, native_id):
    """Allocate independent native IDs, including globally unique message IDs."""
    copied = copy.deepcopy(payload)
    copied['info']['id'] = native_id
    identities = {message['id']: 'msg_' + hashlib.sha256((native_id + '\0' + str(index)).encode()).hexdigest()[:32]
                  for index, message in enumerate(copied['messages'])}
    copied['messages'] = [dict(_references(message, identities), id=identities[message['id']]) for message in copied['messages']]
    return copied


def import_session(manager, home, payload, cwd):
    # Native CLI imports a private file and publishes the session transactionally.
    # stdout is not trusted as evidence: the caller exports the ID to verify it.
    with tempfile.TemporaryDirectory(prefix='agent-opencode-import-') as directory:
        path = Path(directory) / 'session.json'
        from agent_manager.storage.files import atomic
        atomic(path, json.dumps(payload, allow_nan=False).encode())
        _invoke(manager, home, cwd, ['session', 'import', str(path), '--standalone', '--directory', str(cwd)])


def validate_arguments(arguments, native_id):
    arguments = list(arguments)
    if len(arguments) < 2 or arguments[1] != 'mini' and not arguments[1].startswith('-'):
        raise Error('Retained OpenCode continuation requires its native interactive terminal')
    selected, standalone = False, False
    mini = arguments[1] == 'mini'
    values = {'--prompt', '--log-level'} | ({'--model', '-m', '--agent', '--replay-limit'} if mini else set())
    flags = {'--print-logs'} | ({'--replay', '--no-replay'} if mini else {'--auto'})
    index = 2 if mini else 1
    while index < len(arguments):
        key, equals, inline = arguments[index].partition('=')
        if key in ('--session', '-s'):
            value = inline if equals else arguments[index + 1] if index + 1 < len(arguments) else None
            if selected or value != native_id: raise Error('OpenCode continuation must resume the selected native copy exactly once')
            selected = True; index += 1 if equals else 2
        elif key == '--standalone' and not equals:
            if standalone: raise Error('OpenCode continuation requires one private native server')
            standalone = True; index += 1
        elif key in values:
            index += 1 if equals else 2
        elif key in flags and not equals:
            index += 1
        else:
            raise Error('Retained OpenCode continuation cannot use positional, fork, server or session overrides')
    if not selected or not standalone:
        raise Error('OpenCode continuation requires the exact selected session and a private native server')


def requested_session(arguments):
    arguments = list(arguments)
    result = None
    index = 1 if arguments[:1] in (['mini'], ['run']) else 0
    while index < len(arguments):
        key, equals, value = arguments[index].partition('=')
        if key in ('--session', '-s'):
            value = value if equals else arguments[index + 1] if index + 1 < len(arguments) else None
            if result or not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
                raise Error('Use one exact native OpenCode session ID')
            result = value; index += 1 if equals else 2
        elif key in ('--model', '-m', '--agent', '--prompt', '--replay-limit', '--log-level') and not equals:
            index += 2
        else: index += 1
    return result


def replace_session(arguments, old, new):
    """Replace only the user's exact native selector, not prompt/config strings."""
    arguments = list(arguments)
    index = 1 if arguments[:1] in (['mini'], ['run']) else 0
    while index < len(arguments):
        key, equals, value = arguments[index].partition('=')
        if key in ('--session', '-s'):
            if equals:
                if value == old: arguments[index] = key + '=' + new
            elif index + 1 < len(arguments) and arguments[index + 1] == old:
                arguments[index + 1] = new
            index += 1 if equals else 2
        elif key in ('--model', '-m', '--agent', '--prompt', '--replay-limit', '--log-level') and not equals:
            index += 2
        else: index += 1
    return arguments
