import unittest
from unittest.mock import Mock

from conversation_manager.adapters.codex import CodexAdapter
from conversation_manager.models import ConversationError
from conversation_manager.transcript import codex_items


class TranscriptTests(unittest.TestCase):
    def snapshot(self):
        return {"thread": {"turns": [{"id": "native-turn", "items": [
            {"id": "user", "type": "userMessage", "content": [{"type": "text", "text": "Hello 世界"}]},
            {"id": "call", "type": "commandExecution", "command": "pwd", "status": "completed", "exitCode": 0},
            {"id": "reply", "type": "agentMessage", "text": "Hello back"},
        ]}]}}

    def test_saved_history_restores_user_and_stable_anchors_without_duplicate_reads(self):
        first = codex_items(self.snapshot(), {"native-turn": "logical-turn"})
        self.assertEqual(first, codex_items(self.snapshot(), {"native-turn": "logical-turn"}))
        self.assertTrue(first["complete"])
        self.assertEqual([i["type"] for i in first["items"]], ["userMessage", "commandExecution", "agentMessage"])
        self.assertEqual(first["items"][0]["text"], "Hello 世界")
        self.assertEqual([i["id"] for i in first["items"]], ["logical-turn:user", "logical-turn:tool:call", "logical-turn:reply"])

    def test_incomplete_history_is_not_presented_as_complete(self):
        for snapshot in ({"thread": {}}, {"thread": {"turns": [{"id": "t", "items": [], "itemsView": "summary"}]}}):
            with self.assertRaises(ConversationError):
                codex_items(snapshot, {})

    def test_live_user_start_and_completion_replace_the_same_message(self):
        core = Mock()
        adapter = CodexAdapter(core, None)
        params = {"threadId": "thread", "turnId": "turn", "item": {
            "id": "user", "type": "userMessage", "content": [{"type": "text", "text": "Hello"}]}}
        for method in ("item/started", "item/completed"):
            adapter.notification(method, params)
        self.assertEqual(core.emit.call_count, 2)
        for call in core.emit.call_args_list:
            self.assertEqual(call.args[:2], ("message.completed", {"role": "user", "item_id": "user", "text": "Hello"}))


class LiveTranscriptTests(unittest.IsolatedAsyncioTestCase):
    async def test_lagging_history_keeps_live_input_and_streaming_avoids_native_reads(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from conversation_manager.transcript import TranscriptView
        events = []
        snapshot = {'thread': {'turns': [{'id': 'native', 'status': 'inProgress', 'items': []}]}}
        async def history(reference, *, cursor=0, limit=500):
            return {'entries': events[cursor:], 'cursor': len(events), 'has_more': False}
        connection = SimpleNamespace(client=SimpleNamespace(history=history),
            history=AsyncMock(side_effect=lambda _: snapshot),
            session_state=AsyncMock(return_value={'active_turn': {'turn_id': 'logical'}}))
        conversation = SimpleNamespace(reference={}, connection=connection)
        view = TranscriptView()
        def event(kind, role, item_id, text):
            events.append({'kind': kind, 'turn_id': 'logical', 'native_turn_id': 'native',
                'history_cursor': len(events) + 1, 'payload': {'role': role, 'item_id': item_id, 'text': text}})
        event('message.completed', 'user', 'u', 'Shared input')
        first = await view.read(conversation)
        self.assertEqual([i['text'] for i in first['items']], ['Shared input'])
        for chunk in ('One', ' two', ' three'):
            event('message.delta', 'assistant', 'a', chunk)
            result = await view.read(conversation)
        self.assertEqual([i['text'] for i in result['items']], ['Shared input', 'One two three'])
        self.assertEqual(connection.history.await_count, 1)
        snapshot['thread']['turns'][0]['items'] = [
            {'id': 'u', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'Shared input'}]},
            {'id': 'a', 'type': 'agentMessage', 'text': 'One two three'}]
        connection.session_state.return_value = {'active_turn': None}
        caught_up = await view.read(conversation)
        self.assertEqual([i['text'] for i in caught_up['items']], ['Shared input', 'One two three'])
        self.assertFalse(caught_up['reconcile_pending'])
        self.assertEqual(connection.history.await_count, 2)

    async def test_missing_user_precedes_known_reply_and_duplicate_ids_are_not_appended(self):
        from conversation_manager.transcript import TranscriptView
        view = TranscriptView()
        view.live['t:u'] = {'id': 't:u', 'turnId': 't', 'type': 'userMessage', 'text': 'Input', 'cursor': 1}
        view.live['t:a'] = {'id': 't:a', 'turnId': 't', 'type': 'agentMessage', 'text': 'Reply', 'cursor': 2}
        saved = [{'id': 't:a', 'turnId': 't', 'type': 'agentMessage', 'text': 'Reply'}]
        merged, _ = view.merge(saved)
        self.assertEqual([i['text'] for i in merged], ['Input', 'Reply'])
        merged_again, _ = view.merge(merged)
        self.assertEqual(merged_again, merged)

    async def test_provider_failures_keep_live_input_and_back_off_reads(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from conversation_manager.transcript import TranscriptView
        event = {'kind': 'message.completed', 'turn_id': 't', 'history_cursor': 1,
                 'payload': {'role': 'user', 'item_id': 'u', 'text': 'Keep this'}}
        async def history(reference, *, cursor=0, limit=500):
            return {'entries': [event] if not cursor else [], 'cursor': 1, 'has_more': False}
        connection = SimpleNamespace(client=SimpleNamespace(history=history), history=AsyncMock(side_effect=OSError('Offline')),
            session_state=AsyncMock(return_value={'active_turn': None}))
        view = TranscriptView()
        for _ in range(4):
            result = await view.read(SimpleNamespace(reference={}, connection=connection))
            self.assertEqual([i['text'] for i in result['items']], ['Keep this'])
            self.assertFalse(result['complete'])
        self.assertEqual(connection.history.await_count, 1)
