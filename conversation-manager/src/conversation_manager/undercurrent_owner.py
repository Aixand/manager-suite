"""Public Undercurrent SDK process ownership with durable start reconciliation."""

from copy import deepcopy
import uuid

from .models import ConversationError, digest
from .runtime import process_options
from .store import Store


class UndercurrentOwner:
    def __init__(self, home, connection, *, process=None, context=None):
        self.store, self.connection = Store(home), deepcopy(connection)
        self.process = process_options(process)
        self.context = dict(context or {})

    def client(self):
        try:
            from undercurrent import Client
        except ImportError as error:
            raise ConversationError(
                "unsupported",
                "Install aixand-conversation-manager[undercurrent] to inspect or stop this runtime",
            ) from error
        return Client(**self.connection)

    def record(self, ref):
        if (
            ref.get("owner") != "conversation-manager-undercurrent"
            or ref.get("connection") != self.connection
        ):
            raise ConversationError(
                "conflict", "Runtime belongs to another process owner"
            )
        row = self.store.get("undercurrent-launch", ref["id"])
        if row is None or row["runtime_ref"] != ref:
            raise ConversationError(
                "not_found", "Runtime is not recorded in this conversation store"
            )
        return row

    async def resolve(self, client, row):
        if row.get("session_id"):
            session = await client.inspect(row["session_id"])
            candidates = [session]
        else:
            candidates = await client.list(
                labels={"cm.launch": row["runtime_ref"]["id"]}
            )
        found = []
        for session in candidates:
            metadata = await client.metadata(session.id)
            if (
                metadata.get("cm.fingerprint") == row["fingerprint"]
                and metadata.get("cm.launch") == row["runtime_ref"]["id"]
            ):
                found.append(session)
        if len(found) != 1:
            raise ConversationError(
                "uncertain",
                "Original daemon launch cannot be identified uniquely; no replacement was started",
                uncertain=True,
                recovery={"runtime_ref": row["runtime_ref"]},
            )
        if not row.get("session_id"):
            row["session_id"] = found[0].id
            self.store.put("undercurrent-launch", row["runtime_ref"]["id"], row)
        return found[0]

    def prepare(self, command, *, cwd, operation_id):
        key = uuid.uuid5(
            uuid.NAMESPACE_URL, self.store.store_id + ":" + operation_id
        ).hex
        ref = dict(
            owner="conversation-manager-undercurrent",
            id=key,
            connection=self.connection,
        )
        intent = dict(
            command=command,
            cwd=cwd,
            process=self.process,
            connection=self.connection,
            context=self.context,
        )
        fingerprint = digest(intent)
        previous = self.store.get("undercurrent-launch", key)
        if previous and previous["fingerprint"] != fingerprint:
            raise ConversationError(
                "conflict", "Daemon launch identity already has different arguments"
            )
        row = previous or dict(
            runtime_ref=ref,
            fingerprint=fingerprint,
            intent=intent,
            session_id=None,
            dispatched=False,
        )
        self.store.put("undercurrent-launch", key, row)
        return ref

    async def start(self, command, *, cwd, operation_id):
        ref = self.prepare(command, cwd=cwd, operation_id=operation_id)
        row = self.record(ref)
        key, fingerprint = ref["id"], row["fingerprint"]
        async with self.client() as client:
            if row["dispatched"]:
                await self.resolve(client, row)
                return ref
            row["dispatched"] = True
            self.store.put("undercurrent-launch", key, row)  # Intent before daemon IO.
            try:
                session = await client.start(
                    command,
                    cwd=cwd,
                    name=self.process.get("name", "conversation-" + key[:12]),
                    labels={
                        **self.process.get("labels", {}),
                        "cm.app": "conversation-manager",
                        "cm.launch": key,
                        **{
                            "cm." + k: str(v)
                            for k, v in self.context.items()
                            if k in ("provider", "connection")
                        },
                    },
                    metadata={
                        **self.process.get("metadata", {}),
                        "cm.launch": key,
                        "cm.fingerprint": fingerprint,
                        **{"cm." + k: str(v) for k, v in self.context.items()},
                    },
                    detection=False,
                    idempotency_key=key,
                )
                row["session_id"] = session.id
                self.store.put("undercurrent-launch", key, row)
                return ref
            except Exception as error:
                # A new bridge has a new cache identity. Never repeat start after
                # a lost reply. Subsequent reads reconcile the private marker.
                raise ConversationError(
                    "uncertain",
                    "Daemon launch outcome is unknown; inspect its recorded identity",
                    uncertain=True,
                    recovery={"runtime_ref": ref},
                ) from error

    async def inspect(self, runtime_ref):
        row = self.record(runtime_ref)
        if not row["dispatched"]:
            return dict(
                runtime_ref=runtime_ref, state="stopped", session_id=None, name=None
            )
        try:
            async with self.client() as client:
                session = await self.resolve(client, row)
                state = session.lifecycle
                return dict(
                    runtime_ref=runtime_ref,
                    state=state,
                    session_id=session.id,
                    name=session.name,
                )
        except ConversationError:
            raise
        except Exception as error:
            raise ConversationError(
                "uncertain",
                "Cannot observe the original daemon session",
                uncertain=True,
                recovery={"runtime_ref": runtime_ref},
            ) from error

    async def stop(self, runtime_ref, *, operation_id):
        row = self.record(runtime_ref)
        if not row["dispatched"]:
            return
        async with self.client() as client:
            session = await self.resolve(client, row)
            if session.lifecycle in ("stopped", "exited", "failed"):
                return
            # Worker signal handlers close the adapter and its native children.
            # Undercurrent retains supervision and performs its own stop policy.
            await client.stop(session.id)
