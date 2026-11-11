"""Read Codex session metadata without reading conversation messages."""
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
import uuid


@dataclass(frozen=True)
class Session:
    id: str
    profile: str
    title: str
    updated: float
    cwd: Path
    home: Path | None = None
    rollout: Path | None = None
    history_mode: str | None = None
    archived: bool = False
    parent_id: str | None = None


def history_location(rollout, fallback):
    if rollout is not None:
        for parent in rollout.parents:
            if parent.name in ("sessions", "archived_sessions"):
                return parent.parent
    return fallback


def identifier(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


def timestamp(value):
    try:
        result = float(value) if isinstance(value, (float, int)) else datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        return result if math.isfinite(result) and 0 <= result <= 253402214400 else 0
    except (ValueError, TypeError, AttributeError, OverflowError):
        return 0


def directory(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        return None
    try:
        return Path(value).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def index_names(home):
    names = {}
    try:
        with (home / 'session_index.jsonl').open() as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                    sid = identifier(item.get('id'))
                    title = item.get('thread_name')
                    if sid and isinstance(title, str) and title.strip():
                        names[sid] = title
                except (ValueError, AttributeError):
                    continue
    except (OSError, UnicodeError):
        pass
    return names


def discover_home(home, profile, cwd=None, *, thread_id=None, include_archived=False):
    home = Path(home).resolve()
    names = index_names(home)
    found, archived = {}, set()
    # Schema inspection supports older Codex versions without assuming every column exists.
    for database in sorted(home.glob('state_*.sqlite'), key=lambda path: int(path.stem[6:]) if path.stem[6:].isdigit() else -1, reverse=True):
        connection = None
        try:
            connection = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.2)
            columns = {row[1] for row in connection.execute('PRAGMA table_info(threads)')}
            if not {'id', 'cwd', 'updated_at'} <= columns:
                continue
            fields = [key for key in ('id', 'cwd', 'updated_at', 'updated_at_ms', 'name', 'title', 'archived', 'rollout_path', 'history_mode') if key in columns]
            query = 'SELECT ' + ', '.join(fields) + ' FROM threads'
            rows = connection.execute(query + ' WHERE id=?', (thread_id,)) if thread_id else connection.execute(query)
            for row in rows:
                item = dict(zip(fields, row))
                sid = identifier(item['id'])
                if not sid:
                    continue
                if item.get('archived') and not include_archived:
                    archived.add(sid)
                    continue
                saved_cwd = directory(item['cwd'])
                if saved_cwd is None or (cwd is not None and saved_cwd != cwd):
                    continue
                updated = timestamp(item['updated_at_ms'] / 1000 if isinstance(item.get('updated_at_ms'), (int, float)) else item['updated_at'])
                title = item.get('name') or names.get(sid) or item.get('title') or sid
                rollout = directory(item.get('rollout_path'))
                mode = item.get('history_mode')
                # Paginated history belongs to its SQLite store, even when a
                # native descendant's transcript was created in another home.
                location = home if mode == 'paginated' else history_location(rollout, home)
                found[sid] = Session(sid, profile, str(title), updated, saved_cwd, location, rollout, mode, bool(item.get("archived")))
            break
        except (sqlite3.Error, OSError):
            continue
        finally:
            if connection is not None:
                connection.close()
    # Also covers sessions not yet indexed in SQLite. Only the first metadata record is read.
    folders = ('sessions', 'archived_sessions') if include_archived else ('sessions',)
    for rollout in (path for folder in folders for path in (home / folder).glob('**/*.jsonl')):
        try:
            with rollout.open() as stream:
                line = stream.readline(1_048_577)
            if len(line) > 1_048_576:
                continue
            record = json.loads(line)
            if record.get('type') != 'session_meta':
                continue
            payload = record['payload']
            sid = identifier(payload.get('id') or payload.get('session_id'))
            saved_cwd = directory(payload.get('cwd'))
            if not sid or sid in archived or (thread_id and sid != thread_id) or saved_cwd is None or (cwd is not None and saved_cwd != cwd):
                continue
            updated = timestamp(rollout.stat().st_mtime)
            previous = found.get(sid)
            mode = payload.get('history_mode') or (previous.history_mode if previous else None)
            # A paginated stub alone cannot identify or reproduce its database
            # history. Do not advertise it as a second, empty destination copy.
            if mode == 'paginated' and previous is None:
                continue
            if previous and previous.rollout is not None and previous.rollout != rollout.resolve():
                continue  # The native SQLite path is authoritative for this ID.
            found[sid] = Session(sid, profile, previous.title if previous else names.get(sid, sid), max(previous.updated if previous else 0, updated), saved_cwd, previous.home if previous else home, rollout.resolve(), mode,
                previous.archived if previous else "archived_sessions" in rollout.relative_to(home).parts)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError):
            continue
    return list(found.values())


def discover(manager, state, cwd=None):
    cwd = cwd.resolve() if cwd is not None else None
    from agent_manager.profiles.identities import for_environment
    rows = [session for identifier in state["environments"]
            for session in discover_profile(manager, for_environment(state, identifier), cwd)]
    return sorted(rows, key=lambda session: (-session.updated, session.profile, session.id))


def discover_profile(manager, profile, cwd=None):
    return discover_home(manager.home(profile), profile, cwd)


def find_thread(manager, profile, thread_id):
    rows = discover_home(manager.home(profile), profile, thread_id=thread_id)
    return rows[0] if rows else None
