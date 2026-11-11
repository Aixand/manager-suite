"""Reusable native Codex wire client. Profile/resource policy belongs to callers."""

from __future__ import annotations

from . import __version__

import asyncio
import json
import logging
import uuid


# thread/read and thread/resume return saved tool output as well as messages.
# Bound individual replies without treating an ordinary long chat as a disconnect.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024


class NativeCodexError(Exception):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


async def start_thread(rpc, cwd, **settings):
    return await rpc("thread/start", {"cwd": str(cwd), **settings})


async def resume_thread(rpc, thread_id, **settings):
    return await rpc("thread/resume", {"threadId": thread_id, **settings})


async def read_thread(rpc, thread_id, *, include_turns=True):
    return await rpc(
        "thread/read", {"threadId": thread_id, "includeTurns": include_turns}
    )


async def start_turn(rpc, thread_id, content, *, message_id=None):
    params = {"threadId": thread_id, "input": content}
    if message_id is not None:
        params["clientUserMessageId"] = message_id
    return await rpc("turn/start", params)


async def interrupt_turn(rpc, thread_id, turn_id):
    return await rpc("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})


class NativeCodexConnection:
    def __init__(
        self,
        socket_path,
        *,
        on_event=None,
        timeout=60.0,
        capabilities=None,
        client_info=None,
        error_class=NativeCodexError,
        max_message_bytes=MAX_MESSAGE_BYTES,
        transport=None,
    ):
        if (
            isinstance(max_message_bytes, bool)
            or not isinstance(max_message_bytes, int)
            or max_message_bytes <= 0
        ):
            raise ValueError("max_message_bytes must be a positive integer")
        self.socket_path = socket_path
        self.max_message_bytes = max_message_bytes
        self.timeout = timeout
        self.on_event = on_event
        self.capabilities = dict(capabilities or {})
        self.error_class = error_class
        self.client_info = client_info or {
            "name": "conversation_manager",
            "title": "Conversation Manager",
            "version": __version__,
        }
        self._socket = None
        self._transport = transport
        self._reader = None
        self._pending = {}
        self._requests = {}
        self._closing = False

    @property
    def connected(self):
        return (
            self._socket is not None
            and self._reader is not None
            and not self._reader.done()
        )

    @property
    def pending_requests(self):
        return [
            {
                "requestId": token,
                "method": row["method"],
                "params": row.get("params", {}),
            }
            for token, row in self._requests.items()
        ]

    async def __aenter__(self):
        if not self.connected:
            await self.connect()
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def connect(self):
        if self.connected:
            return
        if self._socket is not None:
            await self.close()
        if self._transport is not None:
            self._socket = self._transport
        else:
            await self._connect_socket()
        self._closing = False
        self._reader = asyncio.create_task(self._read())
        try:
            self.initialization = await self.request(
                "initialize",
                {"clientInfo": self.client_info,
                 **({"capabilities": self.capabilities} if self.capabilities else {})},
            )
            await self._socket.send(json.dumps({"method": "initialized"}))
        except BaseException:
            await self.close()
            raise

    async def _connect_socket(self):
        try:
            from aixand_local_runtime.websocket import connect as unix_connect
        except ImportError as error:
            raise self.error_class(
                "Install aixand-conversation-manager[native-codex] to connect to app-server"
            ) from error
        deadline = asyncio.get_running_loop().time() + self.timeout
        while True:
            try:
                self._socket = await unix_connect(
                    self.socket_path,
                    compression=None,
                    open_timeout=min(3, self.timeout),
                    close_timeout=2,
                    max_size=self.max_message_bytes,
                    max_queue=4,
                )
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if asyncio.get_running_loop().time() >= deadline:
                    raise self.error_class(
                        "App-server socket is unavailable; inspect the retained managed run"
                    ) from None
                await asyncio.sleep(0.1)

    def _emit(self, message):
        if self.on_event is not None:
            try:
                self.on_event(message)
            except Exception:
                # Application presentation failures must not kill a shared
                # connection or strand unrelated RPCs and running threads.
                logging.getLogger(__name__).exception('App-server event handler failed for %s', message.get('method'))
                if message.get('method') != 'connection/notificationError':
                    try:
                        self.on_event({'method': 'connection/notificationError', 'params': {
                            'threadId': (message.get('params') or {}).get('threadId'),
                            'error': 'An agent update could not be applied. Retry the connection to refresh its state; the worker was not stopped.'}})
                    except Exception:
                        logging.getLogger(__name__).exception('App-server notification error handler failed')

    async def _read(self):
        from websockets.exceptions import ConnectionClosed
        from .stdio import MessageTooLarge

        failure = "App-server disconnected; refresh before continuing"
        try:
            async for raw in self._socket:
                if len(raw if isinstance(raw, bytes) else raw.encode('utf-8')) > self.max_message_bytes:
                    raise MessageTooLarge()
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError("Invalid app-server message")
                if "method" in message:
                    if "id" in message:
                        if len(self._requests) >= 128:
                            raise ValueError("App-server request limit reached")
                        token = uuid.uuid4().hex
                        self._requests[token] = message
                        self._emit(
                            {
                                "requestId": token,
                                "method": message["method"],
                                "params": message.get("params", {}),
                            }
                        )
                    else:
                        params = message.get("params") or {}
                        if message["method"] == "serverRequest/resolved":
                            for token, row in list(self._requests.items()):
                                if row["id"] == params.get("requestId"):
                                    del self._requests[token]
                        elif message["method"] == "turn/completed":
                            for token, row in list(self._requests.items()):
                                if row.get("params", {}).get("threadId") == params.get(
                                    "threadId"
                                ):
                                    del self._requests[token]
                        self._emit(message)
                elif "id" in message:
                    future = self._pending.get(message["id"])
                    if future is not None and not future.done():
                        if "error" in message:
                            detail = message["error"]
                            error = self.error_class(
                                    str(
                                        detail.get(
                                            "message", "App-server request rejected"
                                        )
                                    )[:1000]
                                )
                            data = detail.get("data") or {}
                            if data.get("code") in ('auth_recovery_pending', 'managed_login_required') and data.get("dispatched") is False:
                                error.code = data['code']
                                error.operation_id = data.get("operation_id")
                                error.dispatched = False
                                error.recovery = {"operation_id": error.operation_id}
                            future.set_exception(error)
                        else:
                            future.set_result(message.get("result"))
        except asyncio.CancelledError:
            raise
        except MessageTooLarge:
            failure = (f"App-server response exceeded the {self.max_message_bytes:,}-byte message limit. "
                       "Increase max_message_bytes before reconnecting; pending requests were not replayed.")
        except ConnectionClosed as error:
            if error.sent is not None and error.sent.code == 1009:
                failure = (
                    f"App-server response exceeded the {self.max_message_bytes:,}-byte message limit. "
                    "Increase max_message_bytes before reconnecting; pending requests were not replayed."
                )
            elif error.sent is not None and 'keepalive' in error.sent.reason.lower():
                failure = 'The chat connection heartbeat timed out. The worker may still be running; retry the connection to inspect it.'
        except Exception:
            # Do not expose socket paths, provider payloads or transport details.
            failure = 'The app-server connection could not process an update. Check the backend logs and reconnect; pending requests were not replayed.'
            logging.getLogger(__name__).exception('App-server reader failed')
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(
                        self.error_class(
                            failure,
                            uncertain=True,
                        )
                    )
            self._requests.clear()
            if not self._closing:
                self._emit({"method": "connection/closed", "params": {'error': failure}})

    async def request(self, method, params=None):
        if not self.connected:
            raise self.error_class("Connect to app-server first")
        if len(self._pending) >= 32:
            raise self.error_class("App-server connection is busy")
        identifier = "am-" + uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[identifier] = future
        try:
            async with asyncio.timeout(self.timeout):
                await self._socket.send(
                    json.dumps(
                        {"id": identifier, "method": method, "params": params or {}}
                    )
                )
                return await future
        except TimeoutError:
            raise self.error_class(
                "App-server request timed out; its outcome is unconfirmed and was not replayed",
                uncertain=True,
            ) from None
        except asyncio.CancelledError:
            raise
        except self.error_class:
            raise
        except Exception:
            raise self.error_class(
                "App-server transport failed; refresh before continuing", uncertain=True
            ) from None
        finally:
            self._pending.pop(identifier, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def respond(self, request_id, result):
        row = self._requests.get(request_id)
        if row is None:
            raise self.error_class(
                "This question or approval has already been resolved"
            )
        try:
            await self._socket.send(json.dumps({"id": row["id"], "result": result}))
        except Exception:
            raise self.error_class(
                "Approval response is unconfirmed; refresh before continuing",
                uncertain=True,
            ) from None
        # Another client can resolve this request while the write yields.
        self._requests.pop(request_id, None)

    async def close(self):
        self._closing = True
        if self._socket is not None:
            await self._socket.close()
        if self._reader is not None:
            if not self._reader.done():
                self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        self._socket = None
        self._reader = None
