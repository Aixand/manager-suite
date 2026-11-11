"""Source adapter integration without other Manager Suite packages or network."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid

from mcp_manager import Manager, McpError
from mcp_manager.storage.platform import check_process


class SourceAdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = Manager(self.root / 'store')
        self.definition = {'command': sys.executable, 'args': ['-V']}

    def test_create_import_settings_and_errors_use_the_owned_library(self):
        created = self.manager.create_managed('Docs', definition=self.definition)
        self.assertEqual(self.manager.resolve_managed('local', created['id'])['definition']['args'], ['-V'])
        with self.assertRaises(McpError) as caught:
            self.manager.create_managed('Docs', definition=self.definition)
        self.assertEqual(caught.exception.code, 'conflict')
        edited = self.manager.source_settings('local', created['id'],
                                             expected_revision=created['revision'], label='Project docs')
        self.assertEqual(edited['label'], 'Project docs')
        source = self.root / 'server.json'
        source.write_text(json.dumps(self.definition), encoding='utf-8')
        imported = self.manager.import_local(source)
        source.unlink()
        self.assertEqual(self.manager.resolve_managed('local', imported['id'])['definition']['args'], ['-V'])
        self.assertEqual(self.manager.source_status('local', imported['id'])['check'], None)
        self.assertEqual(self.manager.check_due_sources(), [])

    def test_retained_source_job_reuses_intent_and_exposes_conflicts(self):
        key = str(uuid.uuid4())
        request = {'name': 'Background docs', 'definition': self.definition}
        row = self.manager.start_source_job('create', request, key=key)
        deadline = time.monotonic() + 20
        try:
            while row['status'] in ('queued', 'running') and time.monotonic() < deadline:
                time.sleep(.02)
                row = self.manager.source_job(row['id'])
            self.assertEqual(row['status'], 'completed', row)
            self.assertEqual(self.manager.start_source_job('create', request, key=key)['result'], row['result'])
            self.assertIn(key, [job['id'] for job in self.manager.source_jobs()])
            with self.assertRaises(McpError) as caught:
                self.manager.start_source_job('create', request | {'name': 'Changed'}, key=key)
            self.assertEqual(caught.exception.code, 'conflict')
            # Cancelling a terminal receipt must not change its result.
            self.assertEqual(self.manager.cancel_source_job(key)['result'], row['result'])
        finally:
            current = self.manager.source_job(key)
            if current['status'] in ('queued', 'running'):
                self.manager.cancel_source_job(key)
            # Terminal status can precede worker exit and log-handle closure.
            deadline = time.monotonic() + 10
            while current.get('pid'):
                try:
                    check_process(current['pid'])
                except ProcessLookupError:
                    break
                self.assertLess(time.monotonic(), deadline, 'Source worker did not exit')
                time.sleep(.02)
