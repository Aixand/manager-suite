"""A process-local history view with the execution profile's real CODEX_HOME.

Linux bind mounts share the original native writer-lock directory; native RPC
uses the original SQLite history store and transcript paths. The destination's
normal SQLite store is never replaced, copied or merged. The profile directory
itself is never replaced: Codex retains its native credential
path, keyring identity and atomic credential-refresh behavior. This module never
opens credential files or writes symlinks into either native home.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import uuid

from agent_manager.storage.files import atomic, private_dir

from agent_manager.errors import Error
from agent_manager.providers import terminal_policy


LOCKS = "thread-writer-locks"


def available():
    return sys.platform == "linux" and shutil.which("bwrap") is not None


@lru_cache(maxsize=16)
def _version(executable, modified, size):
    # Only called when the user requests an actual continuation. A temporary
    # empty home keeps the version probe separate from both native logins.
    from agent_manager.runtime.native.profile_environment import environment
    from agent_manager.providers.policy import cli_version
    try:
        with tempfile.TemporaryDirectory(prefix="agent-codex-version-") as folder:
            variables = environment("codex", os.environ)
            variables.update(CODEX_HOME=folder, CODEX_SQLITE_HOME=folder)
            result = subprocess.run([executable, "--version"], env=variables,
                cwd=folder, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, timeout=5, check=False)
        version = cli_version(result.stdout) if result.returncode == 0 else None
        return terminal_policy.supports_continuation('codex', version)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return False


def _native_version(manager):
    program = shutil.which(manager.config["executable"])
    if program is None:
        raise Error("Configured Codex executable was not found")
    program = Path(program).resolve()
    stat = program.stat()
    if not _version(str(program), stat.st_mtime_ns, stat.st_size):
        raise Error(terminal_policy.version_error('codex'))
    return program


def _location(path, *, directory):
    if path.is_symlink() or (path.exists() and (path.is_dir() != directory or (not directory and not path.is_file()))):
        raise Error("Codex history mount points must be native files or directories, without symlinks")


def _sqlite_location(home, cwd):
    # A source with redirected SQLite needs a separate discovery adapter. Never
    # guess that a leftover database in CODEX_HOME is the original session store.
    files = [home / "config.toml", *home.glob("*.config.toml")]
    files.extend(parent / ".codex" / "config.toml" for parent in (cwd, *cwd.parents))
    def check(value):
        if not isinstance(value, dict): return
        for key, item in value.items():
            if key == "sqlite_home":
                if not isinstance(item, str) or (cwd / Path(item).expanduser()).resolve() != home:
                    raise Error("Cross-profile continuation does not support a redirected sqlite_home")
            elif isinstance(item, dict): check(item)
    for path in files:
        if path.is_file():
            try: check(tomllib.loads(path.read_text(encoding="utf-8")))
            except (OSError, UnicodeError, tomllib.TOMLDecodeError):
                raise Error("Could not validate the native Codex storage configuration") from None


def ready_store(home):
    """A complete native index must not backfill another profile's rollouts.

    Inspect metadata only. Native Codex remains the sole writer of its schema,
    paginated history, queue and goal databases.
    """
    databases = sorted(home.glob("state_*.sqlite"), key=lambda p:
        int(p.stem[6:]) if p.stem[6:].isdigit() else -1, reverse=True)
    if not databases:
        raise Error("The source Codex home has no native SQLite session store")
    database = databases[0]
    if database.is_symlink():
        raise Error("The source Codex SQLite database must not be a symlink")
    connection = None
    try:
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=.2)
        row = connection.execute("SELECT status FROM backfill_state WHERE id=1").fetchone()
        if row is None or row[0] != "complete":
            raise Error("Finish native Codex history indexing in the original profile before continuing across profiles")
    except (OSError, sqlite3.Error):
        raise Error("Could not verify the original native Codex history index") from None
    finally:
        if connection is not None: connection.close()


def _writer_owner(path):
    """Best-effort Linux diagnostics; the native flock remains authoritative."""
    try:
        info = path.stat()
        for row in Path('/proc/locks').read_text().splitlines():
            fields = row.split()
            if len(fields) < 6 or fields[1:4] != ['FLOCK', 'ADVISORY', 'WRITE']:
                continue
            major, minor, inode = fields[5].split(':')
            if (int(major, 16), int(minor, 16), int(inode)) == (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino):
                return ' (writer PID ' + str(int(fields[4])) + ')'
    except (OSError, ValueError):
        pass
    return ''


def _idle(source, thread_id, *, timeout=2):
    import fcntl
    # This is a fast busy check, not a transferred lock. The native resumed
    # process acquires the SAME directory/inodes, closing the handoff race.
    try:
        with (source / "thread-writer-locks" / ".coordination.lock").open("rb") as coordination:
            import time
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(coordination, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise Error("Codex conversation coordination is busy; retry shortly") from None
                    time.sleep(.05)
            path = source / "thread-writer-locks" / (thread_id + ".lock")
            if path.is_symlink(): raise Error("Codex writer lock must not be a symlink")
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "rb") as writer:
                fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise Error("The selected conversation is still open in another Codex process"
            + _writer_owner(path) + "; close it and retry") from None
    except OSError:
        raise Error("Could not inspect the original Codex conversation writer locks") from None


def _arguments(arguments):
    # Config can supply additional literal argv. Do not let it attach to a
    # daemon holding a different login or select a different conversation.
    for item in arguments:
        if item in ("--remote", "--daemon", "--last", "--all") or item.startswith(("--remote=", "--daemon=")):
            raise Error("Cross-profile continuation requires a local exact-session launch without a shared daemon")


def _server_arguments(arguments, thread_id, cwd):
    # These values are converted to native config so the fresh server receives
    # the same settings as an ordinary local terminal.
    flags = {"-m": "model", "--model": "model", "-s": "sandbox_mode",
             "--sandbox": "sandbox_mode", "-a": "approval_policy", "--ask-for-approval": "approval_policy"}
    result, model, index = [], None, 1
    while index < len(arguments):
        item = arguments[index]
        if item == "resume" and arguments[index + 1:index + 2] == [thread_id]:
            index += 2
            continue
        if item == "--no-daemon":
            index += 1
            continue
        if item in ("--yolo", "--dangerously-bypass-approvals-and-sandbox"):
            # Preserve an explicitly selected native policy on the owned server;
            # the app-server parser itself does not accept TUI bypass flags.
            result.extend(("-c", 'approval_policy="never"',
                           "-c", 'sandbox_mode="danger-full-access"'))
            index += 1
            continue
        key, equals, inline = item.partition("=")
        if key in flags or key in ("-c", "--config", "--enable", "--disable", "-C", "--cd"):
            if not equals and index + 1 >= len(arguments):
                raise Error("Missing native continuation option value")
            value = inline if equals else arguments[index + 1]
            index += 1 if equals else 2
            if key in flags:
                result.extend(("-c", flags[key] + "=" + json.dumps(value)))
                if flags[key] == "model": model = value
            elif key in ("-C", "--cd"):
                if (cwd / Path(value).expanduser()).resolve() != cwd:
                    raise Error("Continuation must use the original session working directory")
            else:
                if key in ("--enable", "--disable"):
                    result.extend(("-c", "features." + value + "=" + ("true" if key == "--enable" else "false")))
                else:
                    # Managed delivery has already validated user overrides and
                    # added its own developer_instructions/hook configuration.
                    result.extend(("-c", value))
            continue
        if item.startswith("-c") and len(item) > 2:
            result.extend(("-c", item[2:]))
            index += 1
            continue
        option = f" {key!r}" if key.startswith("-") else ""
        raise Error(f"This native terminal option{option} is not supported for cross-profile continuation; configure persistent settings in the selected profile's native home")
    return result, model


@dataclass(frozen=True)
class HistoryView:
    source: Path
    destination: Path
    bwrap: str
    thread_id: str
    rollout: Path
    cwd: Path
    program: Path

    def command(self, manager, arguments):
        from agent_manager.runtime.native.arguments import command, working_directory
        _arguments(arguments)
        selected = command(arguments[1:])
        if selected[:2] != ["resume", self.thread_id]:
            raise Error("Cross-profile continuation must resume the exact original native session")
        if working_directory(arguments[1:], self.cwd) != self.cwd:
            raise Error("Continuation must use the original session working directory")
        # Preserve every supported flag, and reject unknown flags rather than
        # silently dropping a sandbox, approval or instruction setting.
        config, model = _server_arguments(arguments, self.thread_id, self.cwd)
        defaults, _ = _server_arguments([arguments[0], *manager.config.get("app_server_args", []),
            "resume", self.thread_id], self.thread_id, self.cwd)
        config = defaults + config
        folder = manager.root / "continuations" / uuid.uuid4().hex
        private_dir(folder)
        descriptor = folder / "launch.json"
        source_lock = self.source / LOCKS / (self.thread_id + ".lock")
        target_lock = self.destination / LOCKS / (self.thread_id + ".lock")
        atomic(descriptor, json.dumps({"program": str(self.program), "home": str(self.destination),
            "history_home": str(self.source),
            "thread_id": self.thread_id, "rollout": str(self.rollout), "cwd": str(self.cwd),
            "source_lock": str(source_lock), "target_lock": str(target_lock),
            "coordination": str(self.source / LOCKS / ".coordination.lock"),
            "server_args": config, "model": model}).encode())
        command = [self.bwrap, "--dev-bind", "/", "/"]
        # Bind the DIRECTORY so native cleanup/recreation always uses the same
        # coordination lock and inode domain, including native subagent threads.
        command.extend(("--bind", str(self.source / LOCKS), str(self.destination / LOCKS)))
        return [*command, "--", sys.executable, "-m",
                "agent_manager.runtime.native.codex_continue", str(descriptor)]


def prepare(manager, destination, saved, *, args=(), cwd=None):
    if not manager.provider_spec.sessions.cross_environment_resume or manager.provider != "codex":
        raise Error("Cross-environment native continuation is not qualified for this provider")
    if not available():
        raise Error("Cross-profile Codex continuation requires Linux, bubblewrap (bwrap), and enabled user namespaces")
    source = Path(saved["history_home"]).resolve()
    destination = Path(destination).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise Error("Cross-profile history homes must be separate, non-nested directories")
    thread_id = str(uuid.UUID(saved["native_id"]))
    _arguments(args)
    program = _native_version(manager)
    directory = Path(cwd).resolve()
    _sqlite_location(source, directory)
    _sqlite_location(destination, directory)
    from agent_manager.runtime.native.codex_history import discover_home
    rows = discover_home(source, "", thread_id=thread_id)
    rollout = rows[0].rollout if len(rows) == 1 else None
    if rollout is None or not rollout.is_file() or rows[0].home != source:
        raise Error("The original Codex transcript path is unavailable or outside its native session store")
    ready_store(source)
    if rows[0].history_mode == "paginated" and not any(source.glob("thread_history_*.sqlite")):
        raise Error("The original Codex paginated history database is unavailable")
    collision = discover_home(destination, "", thread_id=thread_id)
    if any(row.rollout != rollout for row in collision):
        raise Error("The destination profile already has a different native conversation with this ID")
    for home in (source, destination):
        _location(home / LOCKS, directory=True)
        _location(home / LOCKS / (thread_id + ".lock"), directory=False)
    if not (source / "sessions").is_dir() or not any(source.glob("state_*.sqlite")):
        raise Error("The source Codex home has no qualified native session store")
    coordination = source / LOCKS / ".coordination.lock"
    if coordination.is_symlink() or not coordination.is_file():
        raise Error("The source Codex home does not expose native conversation writer locks")
    _idle(source, thread_id)
    # The directory is masked only inside this continuation's namespace. Other
    # destination processes keep their original lock directory and databases.
    (destination / LOCKS).mkdir(mode=0o700, exist_ok=True)
    return HistoryView(source, destination, str(Path(shutil.which("bwrap")).resolve()), thread_id, rollout, directory, program)
