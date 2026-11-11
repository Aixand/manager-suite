"""Terminal receipts, report artifacts and durable history share one commit."""

import sqlite3
import tempfile
import unittest
import uuid

from conversation_manager import ConversationError
from conversation_manager.models import receipt
from conversation_manager.store import Store


class DurabilityTests(unittest.TestCase):
    def test_terminal_commit_rolls_back_together_and_survives_replay_pruning(self):
        with tempfile.TemporaryDirectory() as home:
            store = Store(home)
            ref = {'connection_id': uuid.uuid4().hex}
            session = dict(session_id=uuid.uuid4().hex, backend_id='claude', adapter_id='claude-sdk',
                           native_session_id=str(uuid.uuid4()), cwd=home, resource_snapshot_ref=None)
            operation, turn = uuid.uuid4().hex, {'turn_id': uuid.uuid4().hex}
            store.claim(operation, {'kind': 'send'})
            terminal = receipt(operation, 'completed', result={'turn_id': turn['turn_id']})
            artifact = {'status': 'completed', 'final_text': 'Exact assigned report'}
            with store.db(True) as db:
                db.execute("CREATE TRIGGER fail_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'fixture crash'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                store.append(ref, 1, 'turn.completed', {}, session=session, turn=turn, receipts=[terminal], artifact=artifact)
            self.assertEqual(store.operation(operation)['status'], 'uncertain')
            self.assertEqual(store.history_page(session)['entries'], [])
            with self.assertRaises(ConversationError):
                store.turn_result(session, turn['turn_id'])
            with store.db(True) as db:
                db.execute('DROP TRIGGER fail_event')
            event = store.append(ref, 1, 'turn.completed', {}, session=session, turn=turn, receipts=[terminal], artifact=artifact)
            store.append_history(session, event)  # Already admitted; must not duplicate.
            with store.db(True) as db:
                db.execute('DELETE FROM events')
            reopened = Store(home)
            self.assertEqual(reopened.operation(operation)['status'], 'completed')
            self.assertEqual(reopened.turn_result(session, turn['turn_id']), artifact)
            self.assertEqual(reopened.history_page(session)['entries'], [event])
            restored = {**session, 'session_id': uuid.uuid4().hex}
            self.assertEqual(reopened.turn_result(restored, turn['turn_id']), artifact)
            with self.assertRaises(ConversationError):
                reopened.turn_result({**session, 'native_session_id': str(uuid.uuid4())}, turn['turn_id'])

    def test_legacy_history_pages_include_the_durable_row_cursor(self):
        with tempfile.TemporaryDirectory() as home:
            store = Store(home)
            session = dict(backend_id='claude', adapter_id='claude-sdk', native_session_id='fixture', cwd=home, resource_snapshot_ref=None)
            store.append_history(session, {'kind': 'message.delta', 'event_id': 'legacy'})
            entry = store.history_page(session)['entries'][0]
            self.assertGreater(entry['history_cursor'], 0)
            self.assertEqual(store.history_page(session, cursor=entry['history_cursor'])['entries'], [])
