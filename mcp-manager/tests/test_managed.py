"""Owned configurations and guarded publisher reconciliation without applications."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp_manager import Manager, McpError


class ManagedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'store'
        self.manager = Manager(self.home)
        self.definition = {'command': sys.executable, 'args': ['-V'], 'env_vars': ['TOKEN']}

    def ensure(self, revision=1, **changes):
        return self.manager.ensure_registered('publisher', self.definition | changes, publisher_revision=revision)

    def test_read_only_inspection_and_constructor_do_not_create_store(self):
        self.assertEqual(self.manager.managed_catalog(), [])
        with self.assertRaises(McpError): self.manager.resolve_managed('publisher')
        self.assertFalse(self.home.exists())

    def test_idempotency_ordering_and_version_reuse(self):
        a = self.ensure()
        self.assertEqual(self.ensure()['revision'], a['revision'])
        b = self.ensure(2, args=['-c','pass'])
        self.assertFalse(b['reconciliation_required'])
        self.assertEqual(self.ensure(0)['revision'], b['revision'])
        with self.assertRaises(McpError): self.ensure(2, args=['different'])
        self.assertEqual(self.manager.resolve_managed('publisher')['definition']['args'], ['-c','pass'])
        self.assertIsNotNone(self.manager.managed_status('publisher','default')['sync_error'])

    def test_user_copy_stays_active_and_args_conflict_is_atomic(self):
        a = self.ensure()
        b = self.manager.save_managed('publisher','default',self.definition | {'args':['user']},expected_revision=a['revision'])
        pending = self.ensure(2,args=['publisher'])
        self.assertTrue(pending['reconciliation_required'])
        preview = self.manager.preview_reconciliation('publisher','default')
        self.assertEqual([row['path'] for row in preview['conflicts']], ['/definition/args'])
        self.assertEqual(self.manager.resolve_managed('publisher')['definition']['args'], ['user'])
        with self.assertRaises(McpError): self.manager.reconcile('publisher','default',expected_revision=b['revision'],take='publisher')
        accepted = self.manager.reconcile('publisher','default',expected_revision=preview['revision'],take='managed')
        self.assertFalse(accepted['reconciliation_required'])
        self.assertEqual(self.ensure(2,args=['publisher'])['revision'], accepted['revision'])

    def test_structural_proposal_and_invalid_resolution(self):
        a=self.ensure()
        self.manager.save_managed('publisher','default',self.definition | {'tool_timeout_sec':99},expected_revision=a['revision'])
        self.ensure(2,args=['publisher'])
        preview=self.manager.preview_reconciliation('publisher','default')
        self.assertFalse(preview['conflicts'])
        self.assertEqual(preview['candidate']['definition']['tool_timeout_sec'],99)
        self.assertEqual(preview['candidate']['definition']['args'],['publisher'])
        result=self.manager.reconcile('publisher','default',expected_revision=preview['revision'])
        self.assertTrue(result['customized'])
        with self.assertRaises(McpError): self.manager.save_managed('publisher','default',{'command':'python','env':{'TOKEN':'secret'}},expected_revision=result['revision'])
        self.assertEqual(self.manager.managed_status('publisher','default')['revision'],result['revision'])

    def test_scalar_values_are_not_merged_as_text(self):
        a=self.ensure(command='before\ncommand')
        self.manager.save_managed('publisher','default',self.definition | {'command':'local\ncommand'},expected_revision=a['revision'])
        self.ensure(2,command='before\nupstream')
        preview=self.manager.preview_reconciliation('publisher','default')
        self.assertEqual([row['path'] for row in preview['conflicts']],['/definition/command'])

    def test_unordered_updates_and_paused_tracking(self):
        self.ensure(None)
        b=self.ensure(None,args=['new'])
        self.assertTrue(b['reconciliation_required'])
        c=self.manager.set_managed_tracking('publisher','default',False,expected_revision=b['revision'])
        with patch.object(self.manager._managed().backend.__class__,'capture',side_effect=AssertionError('must not capture')):
            self.assertEqual(self.ensure(8)['revision'],c['revision'])

    def test_history_rollback_and_source_loss(self):
        path=Path(self.temp.name)/'server.json'; path.write_text(json.dumps(self.definition))
        a=self.manager.ensure_registered('publisher',path,publisher_revision=1)
        path.unlink()
        self.assertEqual(self.manager.resolve_managed('publisher')['digest'],a['active_digest'])
        b=self.ensure(2,args=['new'])
        c=self.manager.rollback_managed('publisher','default',a['revision'],expected_revision=b['revision'])
        self.assertEqual(c['baseline'],b['baseline'])
        self.assertTrue(c['customized'])
        self.assertEqual(len(self.manager.managed_history('publisher','default')),3)
        self.assertEqual(self.manager.resolve_managed('publisher',revision=b['revision'])['definition']['args'],['new'])

    def test_adoption_does_not_invent_a_baseline(self):
        a=self.manager.adopt_legacy('publisher','default',self.definition | {'args':['user']},self.definition,publisher_revision=1)
        self.assertFalse(a['baseline_known'])
        self.assertTrue(a['reconciliation_required'])
        result=self.manager.reconcile('publisher','default',expected_revision=a['revision'],take='managed')
        self.assertTrue(result['baseline_known'])

    def test_concurrent_saves_have_one_winner(self):
        a=self.ensure()
        def save(timeout):
            try:
                return self.manager.save_managed('publisher','default',self.definition | {'tool_timeout_sec':timeout},expected_revision=a['revision'])
            except McpError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(save,[30,45]))
        self.assertEqual(sum(isinstance(row,dict) for row in results),1)
        self.assertIn('conflict',results)

    def test_corrupt_objects_fail_and_preview_is_bounded(self):
        a=self.ensure(args=['x'*100000])
        view=self.manager.managed_view('publisher','default',path='/definition',limit=1024)
        self.assertLessEqual(len(view['sides']['managed']['text']),1024)
        self.assertIsNotNone(view['sides']['managed']['next_offset'])
        path=self.home/'objects'/(a['active_digest'][7:]+'.json')
        path.write_text('{}')
        with self.assertRaises(McpError) as error: self.manager.resolve_managed('publisher')
        self.assertEqual(error.exception.code,'integrity')

    def test_legacy_bootstrap_cannot_register_defaults_or_change_saved_definitions(self):
        self.assertEqual(self.manager.bootstrap_defaults(), [])
        self.assertFalse(self.home.exists())
        self.manager.add('sonar', {'kind': 'sonar', 'command': sys.executable})
        before = (self.home / 'registry.json').read_bytes()
        self.assertEqual(self.manager.bootstrap_defaults(), [])
        self.assertEqual((self.home / 'registry.json').read_bytes(), before)
        self.assertEqual(self.manager.managed_catalog(), [])

    def test_concurrent_bootstrap_is_idempotent(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.manager.bootstrap_defaults(), range(4)))
        self.assertEqual(results, [[], [], [], []])
        self.assertFalse(self.home.exists())

    def test_inspection_retains_identity_and_does_not_start(self):
        a=self.ensure()
        with patch('subprocess.Popen',side_effect=AssertionError('must not launch')):
            first=self.manager.inspect_managed('publisher','default',self.temp.name)
        self.ensure(2,args=['new'])
        second=self.manager.inspect_managed('publisher','default',self.temp.name)
        self.assertEqual(list(first['servers']),list(second['servers']))
        self.assertEqual(first['resolution']['revision'],a['revision'])

    def test_cli_lifecycle_works_without_agent_or_suite(self):
        def run(*args):
            result=subprocess.run([sys.executable,'-m','mcp_manager','--home',str(self.home),'managed',*args],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            return json.loads(result.stdout)
        a=run('ensure','publisher','default','--definition',json.dumps(self.definition),'--publisher-revision','1')
        run('save','publisher','default','--definition',json.dumps(self.definition | {'args':['mine']}),'--expected-revision',a['revision'])
        run('ensure','publisher','default','--definition',json.dumps(self.definition | {'args':['upstream']}),'--publisher-revision','2')
        preview=run('preview','publisher','default')
        run('apply','publisher','default','--take','managed','--expected-revision',preview['revision'])
        self.assertEqual(run('resolve','publisher','default')['definition']['args'],['mine'])
