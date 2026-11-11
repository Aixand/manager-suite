"""Check public history flags with local metadata and no provider process."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
import uuid

from agent_manager import Client
from agent_manager.providers.opencode import database


class SessionHistoryFlagTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)

    def client(self, provider):
        client = Client(provider, root=self.base / provider, runtime=SimpleNamespace())
        client.manager.new_profile('work')
        return client

    async def test_archived_codex_history_is_explicit_and_keeps_stable_references(self):
        client = self.client('codex')
        home = client.manager.home('work')
        native_id = str(uuid.uuid4())
        folder = home / 'archived_sessions'
        folder.mkdir(parents=True)
        path = folder / (native_id + '.jsonl')
        path.write_text(json.dumps(dict(type='session_meta', payload=dict(id=native_id, cwd=str(self.base)))) + '\n')
        self.assertEqual(await client.sessions(), [])
        rows = await client.sessions(include_archived=True)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['archived'])
        self.assertEqual(rows[0]['native_id'], native_id)
        self.assertEqual(rows[0]['rollout_path'], str(path))
        await client.alias_profile('alias', 'work')
        self.assertEqual([row['ref'] for row in await client.sessions(include_archived=True)], [rows[0]['ref']])
        self.assertEqual(await client.sessions(refresh=False), [])

    async def test_opencode_archives_and_children_have_separate_flags(self):
        client = self.client('opencode')
        path = database(client.manager, client.manager.home('work'))
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE session_v2 (id TEXT,title TEXT,time_updated INTEGER,directory TEXT,parent_id TEXT,time_archived INTEGER)')
            for native_id, parent, archived in [('ses_root', None, None), ('ses_child', 'ses_root', None), ('ses_archive', None, 1)]:
                db.execute('INSERT INTO session_v2 VALUES (?,?,?,?,?,?)', (native_id, native_id, 1000, str(self.base), parent, archived))
        async def ids(**flags):
            return {row['native_id'] for row in await client.sessions(**flags)}
        self.assertEqual(await ids(), {'ses_root'})
        self.assertEqual(await ids(include_archived=True), {'ses_root', 'ses_archive'})
        self.assertEqual(await ids(include_children=True), {'ses_root', 'ses_child'})
        self.assertEqual(await ids(include_archived=True, include_children=True), {'ses_root', 'ses_child', 'ses_archive'})
        self.assertEqual(await ids(refresh=False), {'ses_root'})

    async def test_history_flags_require_booleans(self):
        client = self.client('codex')
        for flags in ({'include_archived': 'yes'}, {'include_children': 1}):
            with self.assertRaises(ValueError):
                await client.sessions(**flags)
