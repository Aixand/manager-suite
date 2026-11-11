"""SQLite intents, references and bounded replay. No mkdir on inspection."""

from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import os
import sqlite3
import uuid

from .models import canonical, decode, digest, operation_key, receipt, ConversationError


def now():
    return datetime.now(timezone.utc).isoformat()


HISTORY_KINDS = {
    "queue.updated",
    "message.delta",
    "message.input",
    "tool.start",
    "tool.update",
    "tool.result",
    "turn.completed",
    "session.usage",
    "session.compacted",
    "command.output",
}


class Store:
    def __init__(self, home=None):
        from .config import state_home

        self.home = state_home(home)
        self.path = self.home / "store.sqlite3"
        self.store_id = digest({"kind": "conversation", "home": str(self.home)})

    @contextmanager
    def db(self, write=False):
        if not write and not self.path.exists():
            yield None
            return
        if write:
            self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(
            str(self.path) if write else self.path.as_uri() + "?mode=ro",
            uri=not write,
            timeout=10,
            isolation_level=None,
        )
        try:
            if write:
                os.chmod(self.path, 0o600)
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, intent TEXT, receipt TEXT);
                    CREATE TABLE IF NOT EXISTS objects(kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id));
                    CREATE TABLE IF NOT EXISTS sequences(connection TEXT PRIMARY KEY, head INTEGER);
                    CREATE TABLE IF NOT EXISTS events(connection TEXT, sequence INTEGER, body TEXT,
                        PRIMARY KEY(connection,sequence));
                    CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, session_key TEXT, body TEXT);
                    CREATE INDEX IF NOT EXISTS history_session ON history(session_key,id);
                """)
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def claim(self, operation_id, intent, recovery=None):
        operation_key(operation_id)
        encoded = canonical(intent).decode()
        with self.db(True) as db:
            row = db.execute(
                "SELECT intent,receipt FROM operations WHERE id=?", (operation_id,)
            ).fetchone()
            if row:
                if row[0] != encoded:
                    raise ConversationError(
                        "conflict",
                        "Operation ID already names a different intent",
                        operation_id=operation_id,
                    )
                return self.operation(operation_id), False
            initial = receipt(
                operation_id,
                "uncertain",
                error=ConversationError(
                    "uncertain",
                    "Intent retained; dispatch/completion has not yet been observed",
                    uncertain=True,
                    operation_id=operation_id,
                    recovery=recovery,
                ).to_dict(),
                recovery=recovery,
            )
            db.execute(
                "INSERT INTO operations VALUES(?,?,?)",
                (operation_id, encoded, canonical(initial).decode()),
            )
            return initial, True

    def operation(self, operation_id):
        operation_key(operation_id)
        with self.db() as db:
            row = (
                db.execute(
                    "SELECT receipt FROM operations WHERE id=?", (operation_id,)
                ).fetchone()
                if db
                else None
            )
        if not row:
            raise ConversationError(
                "not_found", "Operation not found", operation_id=operation_id
            )
        result = decode(row[0])
        # A native create acknowledgment is evidence even if the session record
        # or final receipt could not be written. This checkpoint cannot mark an
        # incomplete operation successful, and never authorizes another create.
        known = self.get("operation-recovery", operation_id)
        if known:
            result["recovery"] = {**result["recovery"], **known["recovery"]}
            if result["result"] is None:
                result["result"] = known["result"]
            if result.get("error"):
                result["error"]["recovery"] = result["recovery"]
        return result

    def operation_intent(self, operation_id):
        operation_key(operation_id)
        with self.db() as db:
            row = (
                db.execute(
                    "SELECT intent FROM operations WHERE id=?", (operation_id,)
                ).fetchone()
                if db
                else None
            )
        if not row:
            raise ConversationError(
                "not_found", "Operation not found", operation_id=operation_id
            )
        return decode(row[0])

    def finish(self, row):
        with self.db(True) as db:
            db.execute(
                "UPDATE operations SET receipt=? WHERE id=?",
                (canonical(row).decode(), row["operation_id"]),
            )
        return row

    def get(self, kind, identifier):
        with self.db() as db:
            row = (
                db.execute(
                    "SELECT body FROM objects WHERE kind=? AND id=?", (kind, identifier)
                ).fetchone()
                if db
                else None
            )
        return decode(row[0]) if row else None

    def put(self, kind, identifier, body):
        with self.db(True) as db:
            db.execute(
                "INSERT OR REPLACE INTO objects VALUES(?,?,?)",
                (kind, identifier, canonical(body).decode()),
            )

    def put_many(self, rows):
        """Commit related configuration/checkpoint records in one local transaction."""
        with self.db(True) as db:
            for kind, identifier, body in rows:
                db.execute(
                    "INSERT OR REPLACE INTO objects VALUES(?,?,?)",
                    (kind, identifier, canonical(body).decode()),
                )

    def connection_preparation(self, reference):
        """Read the original launch intent, including records from older workers."""
        with self.db() as db:
            row = db.execute(
                "SELECT intent FROM operations WHERE json_extract(intent, '$.method')='launch' "
                "AND json_extract(receipt, '$.result.connection_ref.connection_id')=?",
                (reference['connection_id'],),
            ).fetchone()
        if row is None:
            raise ConversationError('not_found', 'The original native launch preparation is unavailable')
        return decode(row[0])['prepared']

    def reserve_connection(self, identifier, prepared):
        with self.db(True) as db:
            if db.execute(
                "SELECT 1 FROM objects WHERE kind='connection' AND id=?", (identifier,)
            ).fetchone():
                raise ConversationError(
                    "conflict", "Connection ID already belongs to a retained launch"
                )
            db.execute(
                "INSERT INTO objects VALUES('connection',?,?)",
                (
                    identifier,
                    canonical(
                        {
                            "prepared": prepared,
                            "connection_ref": None,
                            "state": "allocated",
                        }
                    ).decode(),
                ),
            )

    @staticmethod
    def history_key(session):
        return digest(
            {
                k: session[k]
                for k in (
                    "backend_id",
                    "adapter_id",
                    "native_session_id",
                    "cwd",
                    "resource_snapshot_ref",
                )
            }
        )

    def append_history(self, session, event):
        if event["kind"] not in HISTORY_KINDS or "history_cursor" in event:
            return
        with self.db(True) as db:
            db.execute(
                "INSERT INTO history(session_key,body) VALUES(?,?)",
                (self.history_key(session), canonical(event).decode()),
            )

    def history_page(self, session, cursor=0, limit=100):
        if (
            isinstance(cursor, bool)
            or not isinstance(cursor, int)
            or cursor < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 500
        ):
            raise ConversationError(
                "validation",
                "History requires a nonnegative cursor and limit between 1 and 500",
            )
        with self.db() as db:
            exists = (
                db
                and db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='history'"
                ).fetchone()
            )
            rows = (
                db.execute(
                    "SELECT id,body FROM history WHERE session_key=? AND id>? ORDER BY id LIMIT ?",
                    (self.history_key(session), cursor, limit + 1),
                ).fetchall()
                if exists
                else []
            )
        entries, size, next_cursor = [], 0, cursor
        for identifier, body in rows[:limit]:
            size += len(body.encode())
            if size > 3 * 1024 * 1024:
                if not entries:
                    raise ConversationError(
                        "message_limit",
                        "A history event exceeds the local IPC page bound",
                    )
                break
            entries.append({**decode(body), "history_cursor": identifier})
            next_cursor = identifier
        return {
            "schema": "conversation-manager/history-1",
            "session_ref": session,
            "entries": entries,
            "cursor": next_cursor,
            "has_more": len(rows) > len(entries),
            "source": "manager-observed",
            "complete_provider_history": False,
        }

    def all(self, kind):
        with self.db() as db:
            rows = (
                db.execute(
                    "SELECT body FROM objects WHERE kind=? ORDER BY rowid", (kind,)
                ).fetchall()
                if db
                else []
            )
        return [decode(row[0]) for row in rows]

    def turn_result(self, session, turn_id):
        operation_key(turn_id)
        saved = self.get("turn-result", self.history_key(session) + ":" + turn_id)
        if not saved:
            raise ConversationError(
                "not_found", "No observed terminal result for this exact turn"
            )
        return saved

    def head(self, ref):
        with self.db() as db:
            row = (
                db.execute(
                    "SELECT head FROM sequences WHERE connection=?",
                    (ref["connection_id"],),
                ).fetchone()
                if db
                else None
            )
        return row[0] if row else 0

    def append(
        self,
        ref,
        epoch,
        kind,
        payload,
        *,
        session=None,
        turn=None,
        native=None,
        native_session_id=None,
        retention=10000,
        receipts=(),
        artifact=None,
    ):
        identifier = ref["connection_id"]
        with self.db(True) as db:
            row = db.execute(
                "SELECT head FROM sequences WHERE connection=?", (identifier,)
            ).fetchone()
            sequence = (row[0] if row else 0) + 1
            event = dict(
                schema="conversation-manager/event-1",
                event_id=uuid.uuid4().hex,
                sequence=sequence,
                transport_epoch=epoch,
                connection_id=identifier,
                session_id=session["session_id"] if session else None,
                native_session_id=session["native_session_id"]
                if session
                else native_session_id,
                turn_id=turn["turn_id"] if turn else None,
                native_turn_id=(turn.get("native_turn_id") if turn else None)
                or (native.get("turnId") if isinstance(native, dict) else None),
                kind=kind,
                payload=payload,
                native=native,
                observed_at=now(),
            )
            if session and kind in HISTORY_KINDS:
                saved = db.execute(
                    "INSERT INTO history(session_key,body) VALUES(?,?)",
                    (self.history_key(session), canonical(event).decode()),
                )
                event["history_cursor"] = saved.lastrowid
                db.execute(
                    "UPDATE history SET body=? WHERE id=?",
                    (canonical(event).decode(), saved.lastrowid),
                )
            for receipt_row in receipts:
                db.execute(
                    "UPDATE operations SET receipt=? WHERE id=?",
                    (canonical(receipt_row).decode(), receipt_row["operation_id"]),
                )
            if artifact is not None:
                artifact_identifier = self.history_key(session) + ":" + turn["turn_id"]
                db.execute(
                    "INSERT OR REPLACE INTO objects VALUES('turn-result',?,?)",
                    (artifact_identifier, canonical(artifact).decode()),
                )
            db.execute(
                "INSERT OR REPLACE INTO sequences VALUES(?,?)", (identifier, sequence)
            )
            db.execute(
                "INSERT INTO events VALUES(?,?,?)",
                (identifier, sequence, canonical(event).decode()),
            )
            db.execute(
                "DELETE FROM events WHERE connection=? AND sequence<=?",
                (identifier, sequence - retention),
            )
        return event

    def page(self, ref, epoch, after=0, session_id=None, limit=500):
        if not isinstance(after, int) or isinstance(after, bool) or after < 0:
            raise ConversationError(
                "validation", "Cursor must be a nonnegative integer"
            )
        with self.db() as db:
            oldest = (
                db.execute(
                    "SELECT MIN(sequence) FROM events WHERE connection=?",
                    (ref["connection_id"],),
                ).fetchone()[0]
                if db
                else None
            )
            rows = (
                db.execute(
                    "SELECT sequence,body FROM events WHERE connection=? AND sequence>? ORDER BY sequence LIMIT ?",
                    (ref["connection_id"], after, limit),
                ).fetchall()
                if db
                else []
            )
        events = []
        if oldest is not None and after < oldest - 1:
            events.append(
                dict(
                    schema="conversation-manager/event-1",
                    event_id=None,
                    sequence=oldest - 1,
                    transport_epoch=epoch,
                    connection_id=ref["connection_id"],
                    session_id=session_id,
                    native_session_id=None,
                    turn_id=None,
                    native_turn_id=None,
                    kind="replay.gap",
                    payload={
                        "oldest_available_sequence": oldest,
                        "requested_cursor": after,
                    },
                    native=None,
                    observed_at=now(),
                )
            )
        for _, body in rows:
            event = decode(body)
            if (
                session_id is None
                or event["session_id"] == session_id
                or event["session_id"] is None
            ):
                events.append(event)
        return {"events": events, "cursor": rows[-1][0] if rows else after}
