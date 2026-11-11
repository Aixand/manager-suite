"""Native app-server transport limits without a provider or model call."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from conversation_manager.native_codex import NativeCodexConnection, NativeCodexError


class NativeWireTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from websockets.asyncio.server import unix_serve

        self.directory = tempfile.TemporaryDirectory(prefix="native-wire-")
        self.path = str(Path(self.directory.name) / "server.sock")
        self.calls = []
        self.clients = []
        self.server = await unix_serve(self.peer, self.path, compression=None)

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        self.server.close()
        await self.server.wait_closed()
        self.directory.cleanup()

    async def peer(self, socket):
        from websockets.exceptions import ConnectionClosed

        self.peer_socket = socket
        try:
            async for raw in socket:
                message = json.loads(raw)
                method = message.get("method")
                self.calls.append(method)
                if method == "initialized":
                    continue
                if method == "turn/start":
                    await socket.close()
                    return
                if method in ("thread/read", "thread/resume"):
                    # Six MiB on the wire; history must not be truncated to fit.
                    result = {
                        "thread": {
                            "id": "saved-thread",
                            "turns": [
                                {
                                    "id": "saved-turn",
                                    "items": [
                                        {
                                            "id": "saved-output",
                                            "type": "agentMessage",
                                            "text": "界" * (2 * 1024 * 1024),
                                        },
                                    ],
                                },
                            ],
                        }
                    }
                else:
                    result = {"ok": True}
                await socket.send(
                    json.dumps(
                        {"id": message["id"], "result": result}, ensure_ascii=False
                    )
                )
        except ConnectionClosed:
            pass

    async def client(self, **options):
        client = NativeCodexConnection(self.path, timeout=5, **options)
        self.clients.append(client)
        await client.connect()
        return client

    async def test_saved_history_above_four_mib_can_be_read_and_resumed(self):
        client = await self.client()
        for method in ("thread/read", "thread/resume"):
            result = await client.request(method, {"threadId": "saved-thread"})
            thread = result["thread"]
            self.assertEqual(thread["id"], "saved-thread")
            self.assertEqual(
                len(thread["turns"][0]["items"][0]["text"]), 2 * 1024 * 1024
            )
            self.assertTrue(client.connected)
            self.assertEqual(await client.request("model/list"), {"ok": True})

    async def test_size_limit_failure_explains_why_refresh_cannot_recover(self):
        client = await self.client(max_message_bytes=1024)
        with self.assertRaises(NativeCodexError) as caught:
            await client.request("thread/read", {"threadId": "saved-thread"})
        self.assertIn("message limit", str(caught.exception))
        self.assertIn("max_message_bytes", str(caught.exception))
        self.assertTrue(caught.exception.uncertain)
        self.assertFalse(client.connected)
        self.assertEqual(self.calls.count("thread/read"), 1)

    async def test_disconnected_mutation_is_not_replayed(self):
        client = await self.client()
        with self.assertRaises(NativeCodexError) as caught:
            await client.request(
                "turn/start", {"threadId": "saved-thread", "input": []}
            )
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(self.calls.count("turn/start"), 1)

    async def test_application_callback_failure_does_not_disconnect_other_rpcs(self):
        events = []
        def on_event(event):
            if event['method'] == 'item/started':
                raise KeyError('Presentation failed')
            events.append(event)
        client = await self.client(on_event=on_event)
        with self.assertLogs('conversation_manager.native_codex', level='ERROR'):
            await self.peer_socket.send(json.dumps({'method': 'item/started', 'params': {'threadId': 'active-thread'}}))
            self.assertEqual(await client.request('model/list'), {'ok': True})
        self.assertTrue(client.connected)
        self.assertEqual(events[-1]['method'], 'connection/notificationError')
        self.assertEqual(events[-1]['params']['threadId'], 'active-thread')
        self.assertIn('worker was not stopped', events[-1]['params']['error'])
        self.assertFalse(any(row['method'] == 'connection/closed' for row in events))

    async def test_disconnect_notification_retains_the_transport_failure_reason(self):
        events = []
        client = await self.client(on_event=events.append, max_message_bytes=1024)
        with self.assertRaises(NativeCodexError):
            await client.request('thread/read', {'threadId': 'saved-thread'})
        closed = next(row for row in events if row['method'] == 'connection/closed')
        self.assertIn('message limit', closed['params']['error'])

    async def test_another_client_can_resolve_an_approval_while_our_response_is_writing(self):
        client = await self.client()
        token = "pending-approval"
        client._requests[token] = {"id": 42, "method": "item/commandExecution/requestApproval", "params": {"threadId": "thread"}}
        async def send(_):
            # The reader processes serverRequest/resolved from the terminal
            # while the SDK's socket write yields to the event loop.
            client._requests.pop(token)
        with patch.object(client._socket, "send", AsyncMock(side_effect=send)):
            await client.respond(token, {"decision": "accept"})
        self.assertEqual(client.pending_requests, [])
        with self.assertRaisesRegex(NativeCodexError, "already been resolved"):
            await client.respond(token, {"decision": "decline"})


if __name__ == "__main__":
    unittest.main()
