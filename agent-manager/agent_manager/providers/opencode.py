"""OpenCode V2 native homes, model discovery and session metadata."""
from agent_manager.profiles.identities import environment_id
import json
import base64
from contextlib import closing, contextmanager
import os
from pathlib import Path
import re
import queue
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from agent_manager.errors import Error
from agent_manager.storage.files import private_dir


class CatalogError(Error):
    """A fixed public diagnostic plus a machine-readable discovery failure."""
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def model_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[^\s/]+/[^\s]+", value):
        raise Error("OpenCode models must use provider/model (optionally #variant)")
    return value


def environment(manager, home):
    home = home.resolve()
    if home == manager.native.resolve():
        # Keep the native default's normal configuration and data locations.
        paths = {key: os.environ.get(key, str(Path.home() / fallback)) for key, fallback in (
            ("XDG_CONFIG_HOME", ".config"), ("XDG_CACHE_HOME", ".cache"),
            ("XDG_STATE_HOME", ".local/state"))}
        paths["XDG_DATA_HOME"] = str(home.parent)
        config = Path(paths["XDG_CONFIG_HOME"]) / "opencode"
        database = home / "opencode.db"
    else:
        paths = {"XDG_" + key.upper() + "_HOME": str(home / key)
                 for key in ("config", "data", "cache", "state")}
        config = home / "config/opencode"
        database = home / "data/opencode/opencode.db"
    # Explicit overrides also protect SDK launches from inherited home settings.
    return paths | {"OPENCODE_CONFIG_DIR": str(config), "OPENCODE_DB": str(database)}


def database(manager, home):
    return Path(environment(manager, home)["OPENCODE_DB"])


def launch_args(manager, profile, args, model=None):
    args = list(args)
    if any(arg == "--server" or arg.startswith("--server=") for arg in args):
        raise Error("Managed OpenCode launches use a private server; --server is not supported")
    # The ordinary entrypoint opens the full-screen TUI. Mini and headless run
    # remain explicit native choices. Full-screen model flags are translated to
    # process-local native configuration when the launch is finalized.
    verb = [args.pop(0)] if args[:1] in (["run"], ["mini"]) else []
    forwarded_model = any(arg in ("--model", "-m") or arg.startswith("--model=") for arg in args)
    resuming = any(arg in ('--session', '-s', '--continue', '-c') or arg.startswith('--session=') for arg in args)
    if not verb and resuming and (model is not None or forwarded_model):
        raise Error('OpenCode full-screen resume keeps the session model; change it inside OpenCode or explicitly use mini with --session')
    selected = model
    if selected is None and (verb or not resuming):
        state = manager.state()
        selected = state.get('models', {}).get(environment_id(state, profile))
    selection = ["--model", model_id(selected)] if selected and (model is not None or not forwarded_model) else []
    if model is not None and forwarded_model:
        raise Error("Specify the model either with --model or in forwarded arguments, not both")
    return [*verb, "--standalone", *manager.saved_launch_args(profile), *selection, *args]


def terminal_launch(arguments, variables):
    """Use native inline configuration for a full-screen launch's model default.

    Arguments are finalized after resource overlays, so MCP, instructions and
    inherited native settings survive. No profile or project file is rewritten.
    """
    arguments = list(arguments)
    if arguments and not arguments[0].startswith('-'): return arguments, variables
    result, selected, index = [], None, 0
    while index < len(arguments):
        item = arguments[index]
        if item == '--':
            result.extend(arguments[index:]); break
        if item in ('--model', '-m'):
            index += 1
            if index >= len(arguments): raise Error('The model option requires a value')
            selected = model_id(arguments[index])
        elif item.startswith('--model='):
            selected = model_id(item.partition('=')[2])
        else:
            result.append(item)
            if item in ('--session', '-s', '--prompt', '--log-level') and index + 1 < len(arguments):
                index += 1; result.append(arguments[index])
        index += 1
    if selected is None: return result, variables
    if '#' in selected:
        raise Error('OpenCode full-screen model defaults do not retain #variants; choose the variant inside OpenCode or explicitly use mini')
    try:
        content = json.loads(variables.get('OPENCODE_CONFIG_CONTENT', '{}'))
        if not isinstance(content, dict): raise ValueError()
    except (ValueError, TypeError):
        raise Error('Invalid inherited OpenCode inline configuration') from None
    content['model'] = selected
    return result, variables | {'OPENCODE_CONFIG_CONTENT': json.dumps(content)}


def _server_output(stream, startup):
    # Windows select() accepts sockets, not subprocess pipes. Keep the bounded
    # line read off the calling thread so a partial line also obeys the timeout.
    with stream:
        try:
            line = stream.readline(65537)
            startup.put_nowait(line)
            # Keep stdout drained while the provider initializes its plugins.
            while stream.read(65536):
                pass
        except (OSError, ValueError, UnicodeError):
            try:
                startup.put_nowait("")
            except queue.Full:
                pass


@contextmanager
def metadata_server(manager, home, overrides, *, cwd, startup_timeout=15, end=None):
    """Private native metadata transport, with bounded reads and no credential API."""
    private_dir(home)
    def timeout(seconds):
        if end is None: return seconds
        from agent_manager.budgets.transport import remaining
        return min(seconds, remaining(end))
    password = secrets.token_urlsafe(32)
    env = manager.launch_environment(home, cwd=cwd) | overrides | {
        "OPENCODE_SERVER_USERNAME": "opencode", "OPENCODE_SERVER_PASSWORD": password,
        "PWD": str(cwd)}
    process = subprocess.Popen([manager.config["executable"], "serve", "--stdio", "--port", "0",
                                "--hostname", "127.0.0.1"],
                               env=env, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True)
    startup = queue.Queue(maxsize=1)
    reader = threading.Thread(target=_server_output, args=(process.stdout, startup), daemon=True,
                              name="opencode-model-discovery")
    reader.start()
    try:
        try:
            line = startup.get(timeout=timeout(startup_timeout))
        except queue.Empty:
            raise CatalogError('timeout', "OpenCode private server startup timed out") from None
        try:
            if len(line) > 65536 or not line.endswith('\n'): raise ValueError()
            parsed = urllib.parse.urlparse(json.loads(line)["url"])
            if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
                    or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
                raise ValueError()
            url = parsed.geturl().rstrip('/')
        except (ValueError, KeyError, TypeError):
            raise CatalogError('invalid-response', "Invalid OpenCode V2 server startup response") from None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        query = urllib.parse.urlencode({"location[directory]": str(cwd)})
        allowed = {'/api/model', '/api/integration', '/api/rpc/aixand.agent.budgets.v2/read'}
        def request(path, body=None):
            if path not in allowed: raise Error('Unsupported native metadata route')
            headers = {"Authorization": "Basic " + base64.b64encode(("opencode:" + password).encode()).decode()}
            if body is not None: headers['Content-Type'] = 'application/json'
            data = json.dumps(body).encode() if body is not None else None
            message = urllib.request.Request(url + path + '?' + query, data=data, headers=headers)
            with opener.open(message, timeout=timeout(10)) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise CatalogError('invalid-response', 'OpenCode metadata exceeds the size limit')
                payload = json.loads(raw)
            if not isinstance(payload, dict): raise CatalogError('invalid-response', 'Invalid OpenCode metadata')
            return payload
        yield request
    finally:
        process.stdin.close()
        if process.poll() is None: process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
        reader.join(timeout=5)


def catalog(manager, home, overrides, *, cwd, startup_timeout=15, end=None):
    """Wait for native model plugins to settle; never start a conversation."""
    with metadata_server(manager, home, overrides, cwd=cwd, startup_timeout=startup_timeout, end=end) as request:
        deadline = time.monotonic() + 15
        previous, unchanged_since = None, time.monotonic()
        while True:
            payload = request('/api/model')
            if not isinstance(payload.get('data'), list):
                raise CatalogError('invalid-response', 'Invalid OpenCode model catalog')
            fingerprint = json.dumps(payload['data'], sort_keys=True)
            now = time.monotonic()
            if fingerprint != previous: previous, unchanged_since = fingerprint, now
            if (payload['data'] and now - unchanged_since >= 2) or now >= deadline: return payload
            if end is not None:
                from agent_manager.budgets.transport import remaining
                time.sleep(min(.25, remaining(end)))
            else: time.sleep(.25)


def sessions(manager, home, profile, cwd=None, *, include_archived=False, include_children=False):
    from agent_manager.runtime.native.codex_history import Session
    path = database(manager, home)
    if not path.exists():
        return []
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            conditions = []
            if not include_children: conditions.append("parent_id IS NULL")
            if not include_archived: conditions.append("time_archived IS NULL")
            query = "SELECT id, title, time_updated, directory, parent_id, time_archived FROM session_v2"
            if conditions: query += " WHERE " + " AND ".join(conditions)
            rows = connection.execute(query).fetchall()
    except sqlite3.Error:
        raise Error("Could not read OpenCode V2 session metadata") from None
    return [Session(sid, profile, title or sid, updated / 1000, Path(directory).resolve(), Path(home),
                    archived=archived is not None, parent_id=parent)
            for sid, title, updated, directory, parent, archived in rows
            if isinstance(sid, str) and re.fullmatch(r"ses_[A-Za-z0-9]+", sid)
            and directory and (cwd is None or Path(directory).resolve() == cwd)]
