"""Native history discovery and terminal table behavior."""
import curses
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from agent_manager import Manager
from agent_manager.interfaces.cli import tui
from agent_manager.runtime.native.codex_history import discover, discover_home


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.config = self.base / 'providers.toml'; self.config.write_text('')
        self.manager = Manager('codex', self.config, self.base / 'homes')
        self.manager.new_profile('work'); self.manager.new_profile('other')

    def rollout(self, profile='work', *, cwd=None, updated=1000):
        identifier = str(uuid.uuid4()); home = self.manager.home(profile)
        directory = home / 'sessions'; directory.mkdir(exist_ok=True)
        path = directory / (identifier + '.jsonl')
        path.write_text(json.dumps({'type': 'session_meta', 'timestamp': updated,
            'payload': {'id': identifier, 'cwd': str(cwd or self.base)}}) + '\ninvalid message body; never parsed\n')
        return identifier, path

    def test_discovery_merges_stores_filters_workspaces_and_excludes_archived_sessions(self):
        first, _ = self.rollout(updated=2000)
        second, _ = self.rollout('other', updated=3000)
        archived, _ = self.rollout(); self.rollout(cwd=self.base / 'child')
        database_only = str(uuid.uuid4())
        with sqlite3.connect(self.manager.home('work') / 'state_5.sqlite') as db:
            db.execute('CREATE TABLE threads (id TEXT,cwd TEXT,updated_at INTEGER,name TEXT,title TEXT,archived INTEGER)')
            db.executemany('INSERT INTO threads VALUES(?,?,?,?,?,?)', [
                (first, str(self.base), 2000, 'Named session', 'Old title', 0),
                (archived, str(self.base), 1000, 'Archived', '', 1),
                (database_only, str(self.base), 500, '', 'Database title', 0)])
        rows = discover(self.manager, self.manager.state(), self.base)
        self.assertEqual([row.id for row in rows], [second, first, database_only])
        self.assertEqual(rows[1].title, 'Named session')
        self.assertEqual(rows[1].profile, 'work')
        self.assertEqual(rows[2].title, 'Database title')
        self.assertFalse(hasattr(rows[1], 'auth'))

    def test_corrupt_database_falls_back_to_metadata_and_native_index_names(self):
        identifier, _ = self.rollout()
        home = self.manager.home('work'); (home / 'state_5.sqlite').write_text('corrupt')
        (home / 'session_index.jsonl').write_text('invalid\n' + json.dumps({'id': identifier, 'thread_name': 'Friendly'}) + '\n')
        (home / 'sessions/broken.jsonl').write_text('invalid')
        rows = discover_home(home, 'work', self.base)
        self.assertEqual([(row.id, row.title) for row in rows], [(identifier, 'Friendly')])

    def test_workspace_symlinks_are_compared_by_canonical_directory(self):
        linked = self.base / 'linked'; linked.symlink_to(self.base, target_is_directory=True)
        identifier, _ = self.rollout(cwd=linked)
        self.assertEqual([row.id for row in discover_home(self.manager.home('work'), 'work', self.base)], [identifier])

    def test_duplicate_native_ids_keep_their_original_home_references(self):
        identifier, path = self.rollout()
        other = self.manager.home('other') / 'sessions'; other.mkdir()
        (other / path.name).write_bytes(path.read_bytes())
        rows = discover(self.manager, self.manager.state(), self.base)
        self.assertEqual([row.id for row in rows], [identifier, identifier])
        self.assertEqual({row.home for row in rows}, {self.manager.home('work'), self.manager.home('other')})


class Screen:
    def __init__(self, keys, dimensions=(12, 120)):
        self.keys, self.dimensions, self.lines = iter(keys), dimensions, []
    def keypad(self, enabled): pass
    def erase(self): pass
    def refresh(self): pass
    def getmaxyx(self): return self.dimensions
    def addstr(self, y, x, text, attr): self.lines.append(text)
    def getch(self): return next(self.keys)


class TuiTests(unittest.TestCase):
    def test_budget_column_and_exhausted_choice_fit_the_minimum_terminal_width(self):
        row = ('profile with a long name', '0% — exhausted', '/a/long/native/home')
        screen = Screen([10], (9, 75))
        with patch.object(curses, 'curs_set'):
            self.assertEqual(tui._screen(screen, 'Profiles', ['Profile', 'Budget', 'Native home'], [row], ''), 0)
        self.assertTrue(any('Budget' in line and 'Native home' in line for line in screen.lines))
        self.assertTrue(any('0% — exhausted' in line for line in screen.lines))

    def test_navigation_paging_cancel_and_control_character_sanitizing(self):
        rows = [('Session\x1b[31m\nname', '/workspace')] * 12
        with patch.object(curses, 'curs_set'):
            screen = Screen([curses.KEY_NPAGE, curses.KEY_DOWN, 10])
            self.assertEqual(tui._screen(screen, 'Sessions', ['Name', 'Working directory'], rows, '/tmp'), 6)
            self.assertTrue(all('\x1b' not in line and '\n' not in line for line in screen.lines))
            self.assertIsNone(tui._screen(Screen([27]), 'Title', ['Name'], [('a',)], ''))
            self.assertIsNone(tui._screen(Screen([ord('q')], (5, 30)), 'Title', ['Name'], [('a',)], ''))
