"""Private, versioned local IPC. Each call is independent of worker lifetime."""
from __future__ import annotations
from aixand_local_runtime import open_local_connection
import asyncio
from .models import canonical, decode, ConversationError

IPC_SCHEMA = "conversation-manager/ipc-1"
LIMIT = 4 * 1024 * 1024


class LocalTransport:
    def __init__(self, ref, timeout=30):
        self.ref, self.timeout, self.closed = ref, timeout, False

    async def call(self, method, params):
        if self.closed:
            raise ConversationError("transport", "Frontend is detached; reconnect first")
        writer = None
        dispatched = False
        try:
            async with asyncio.timeout(self.timeout):
                reader, writer = await open_local_connection(self.ref["transport"]["endpoint"], limit=LIMIT)
                writer.write(canonical({"schema": IPC_SCHEMA, "connection_ref": self.ref,
                                        "method": method, "params": params}) + b"\n")
                dispatched = True
                await writer.drain()
                raw = await reader.readline()
                if not raw:
                    raise ConnectionError()
                response = decode(raw)
                if response.get("schema") != IPC_SCHEMA:
                    raise ConnectionError()
                if response.get("error"):
                    error = response["error"]
                    raise ConversationError(error["code"], error["message"], uncertain=error.get("uncertain", False),
                                      operation_id=error.get("operation_id"), recovery=error.get("recovery"))
                return response["result"]
        except ConversationError:
            raise
        except (OSError, ConnectionError, TimeoutError, ValueError, KeyError) as exc:
            mutation = "operation_id" in params
            raise ConversationError("uncertain" if dispatched and mutation else "transport",
                "Recorded worker unavailable; inspect retained operation/runtime, then reconnect without replay",
                uncertain=dispatched and mutation, operation_id=params.get("operation_id"),
                recovery={"connection_ref": self.ref}) from exc
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, ConnectionError):
                    pass

    async def close(self):
        self.closed = True


class LocalConnector:
    def __init__(self, *, timeout=30):
        self.timeout = timeout

    async def attach(self, connection_ref):
        if connection_ref.get("schema") != "conversation-manager/connection-ref-1" or connection_ref.get("transport", {}).get("kind") != "conversation-manager-local":
            raise ConversationError("unsupported", "Supply a connector for this transport")
        handle = LocalTransport(connection_ref, self.timeout)
        await handle.call("hello", {})
        return handle


def connector_factory(options):
    return LocalConnector(**options)
