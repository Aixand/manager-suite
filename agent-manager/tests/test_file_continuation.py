"""Cross-account terminal handoffs with synthetic native histories and logins."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from agent_manager import Manager, Client, Error
from agent_manager.profiles.operations import delete_profile
from agent_manager.profiles.sessions import continue_session, remember, listing, for_resume
from agent_manager.runtime.execution import launch_home
from agent_manager.runtime.native.file_continuation import prepare, acquire_session
from agent_manager.runtime.runs import update_run, read_run, finish_run
from agent_manager.storage.locks import locks
from agent_manager.runtime.session_leases import release
from tests.support import FakeRuntime


NATIVE = r'''
import json,os,pathlib,sys,time,uuid,signal
provider=__FIXTURE_PROVIDER__
args=sys.argv[1:]
if args == ['--version']:
 print(__FIXTURE_VERSION__);sys.exit(0)
home=pathlib.Path(os.environ['CLAUDE_CONFIG_DIR' if provider=='claude' else 'COPILOT_HOME'])
def value(flag):
 for i,arg in enumerate(args):
  if arg.startswith(flag+'='):return arg.split('=',1)[1]
  if arg==flag:return args[i+1]
 return None
resume=value('--resume') or value('-r')
sid=value('--session-id')
if resume:
 if provider=='claude':
  path=pathlib.Path(resume) if '/' in resume else next((home/'projects').glob('*/'+resume+'.jsonl'))
  sid=path.stem
 else:sid=resume;path=home/'session-state'/sid/'events.jsonl'
else:
 sid=sid or str(uuid.uuid4())
 path=home/'projects/fixture-project'/(sid+'.jsonl') if provider=='claude' else home/'session-state'/sid/'events.jsonl'
 path.parent.mkdir(parents=True,exist_ok=True)
 if provider=='copilot':(path.parent/'workspace.yaml').write_text('cwd: '+str(pathlib.Path.cwd()))
if os.environ.get('FIXTURE_IGNORE_TERM'):signal.signal(signal.SIGTERM,signal.SIG_IGN)
row=dict(pid=os.getpid(),id=sid,home=str(home),history=str(path.resolve()),cwd=str(pathlib.Path.cwd()),
 account=(home/'fixture-account').read_text(),args=args,
 overrides=[k for k in ('ANTHROPIC_API_KEY','CLAUDE_CODE_OAUTH_TOKEN','GITHUB_TOKEN','COPILOT_PROVIDER_API_KEY') if k in os.environ])
(home/'observed.json').write_text(json.dumps(row))
with path.open('a') as stream:stream.write(json.dumps(dict(id=sid,account=row['account']))+'\n')
if os.environ.get('FIXTURE_SLEEP'):time.sleep(float(os.environ['FIXTURE_SLEEP']))
'''


class Fixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agent-file-handoff-'); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cli = self.base / self.provider
        version = '2.1.316' if self.provider == 'claude' else 'GitHub Copilot CLI 1.0.105.'
        self.cli.write_text('#!' + sys.executable + '\n' + NATIVE.replace('__FIXTURE_PROVIDER__', repr(self.provider)).replace('__FIXTURE_VERSION__', repr(version)))
        self.cli.chmod(0o700)
        self.config = self.base / 'providers.toml'
        self.config.write_text(f'[providers.{self.provider}]\nexecutable=' + json.dumps(str(self.cli)) + '\n')
        self.manager = Manager(self.provider, self.config, self.base / 'manager')
        for name in ('source','target','third'):
            self.manager.new_profile(name)
            (self.manager.home(name) / 'fixture-account').write_text(name)
        self.source = self.manager.home('source'); self.target = self.manager.home('target')
        self.sid = str(uuid.uuid4())
        if self.provider == 'claude': self.path = self.source / 'projects/fixture-project' / (self.sid + '.jsonl')
        else: self.path = self.source / 'session-state' / self.sid / 'events.jsonl'
        self.path.parent.mkdir(parents=True); self.path.write_text('SOURCE_HISTORY\n')
        if self.provider == 'copilot': (self.path.parent / 'workspace.yaml').write_text('cwd: ' + str(self.base))
        self.saved = remember(self.manager, 'source', self.sid, cwd=self.base)

    def resume(self, profile='target'):
        return continue_session(self.manager, self.saved['ref'], profile=profile)

    def view(self, profile='target'):
        return prepare(self.manager, profile, self.saved, args=['--resume',self.sid], cwd=self.base)

    def observed(self, profile):
        return json.loads((self.manager.home(profile) / 'observed.json').read_text())

    def test_round_trip_keeps_one_history_and_uses_each_destination_account(self):
        inode = self.path.stat().st_ino
        for profile in ('target','third','source','target'):
            self.assertEqual(self.resume(profile),0)
            row = self.observed(profile)
            self.assertEqual((row['id'],row['history'],row['account'],row['cwd']), (self.sid,str(self.path),profile,str(self.base)))
            self.assertEqual(self.path.stat().st_ino,inode)
        self.assertEqual(len(self.path.read_text().splitlines()),5)
        self.assertEqual(len(listing(self.manager)),1)
        self.assertEqual(self.manager.state()['sessions'][self.saved['ref']]['history_home'],str(self.source))
        for profile in ('source','target','third'):
            self.assertEqual((self.manager.home(profile)/'fixture-account').read_text(),profile)

    def test_ambient_credentials_cannot_override_destination(self):
        with patch.dict(os.environ,ANTHROPIC_API_KEY='unselected',CLAUDE_CODE_OAUTH_TOKEN='unselected',GITHUB_TOKEN='unselected',COPILOT_PROVIDER_API_KEY='unselected'):
            self.assertEqual(self.resume(),0)
        forbidden = ('ANTHROPIC_API_KEY','CLAUDE_CODE_OAUTH_TOKEN') if self.provider=='claude' else ('GITHUB_TOKEN','COPILOT_PROVIDER_API_KEY')
        self.assertFalse(set(forbidden) & set(self.observed('target')['overrides']))

    def test_stale_or_unknown_version_refuses_before_publishing_route(self):
        self.cli.write_text(self.cli.read_text().replace('2.1.316','2.1.317').replace('1.0.105','1.0.106'))
        with self.assertRaisesRegex(Error,'requires native CLI'):self.resume()
        self.assertNotIn('session_routes',self.manager.state())
        self.assertEqual(self.path.read_text(),'SOURCE_HISTORY\n')

    def test_prepared_command_keeps_captured_environment_after_rename_and_reuse(self):
        target = self.manager.resolve_profile('target')
        with launch_home(self.manager,target,args=['--resume',self.sid],cwd=self.base,thread_id=self.sid,source_session=self.saved) as plan:
            self.manager.rename('target','company');delete_profile(self.manager,'company')
            self.manager.new_profile('target');(self.manager.home('target')/'fixture-account').write_text('replacement')
            plan.claim(self.manager,self.manager.state(),target)
            command = self.manager.launch_command(plan.home,[str(self.cli),'--resume',self.sid])
        completed=subprocess.run(command,env=self.manager.launch_environment(plan.home),cwd=self.base,capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertEqual(json.loads((self.target/'observed.json').read_text())['account'],'target')
        self.assertFalse((self.manager.home('target')/'observed.json').exists())

    def test_existing_managed_writer_and_unknown_native_picker_block_handoff(self):
        for native_id in (self.sid,None):
            state=self.manager.state();state['runs']['busy']=dict(agent='source',environment_id=self.manager.resolve_profile('source').environment_id,home=str(self.source),thread_id=native_id,pid=os.getpid())
            self.manager.save(state)
            with self.assertRaisesRegex(Error,'active managed run'):self.resume()
            state=self.manager.state();del state['runs']['busy'];self.manager.save(state)
        self.assertFalse((self.target/'observed.json').exists())

    def test_lifetime_lock_refuses_a_second_native_wrapper(self):
        view=self.view(); command=view.command(self.manager,[str(self.cli),'--resume',self.sid])
        with locks(self.source/'.agent-manager-continuations',self.saved['ref']):
            result=subprocess.run(command,cwd=self.base,env=self.manager.launch_environment(self.target),capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.path.read_text(),'SOURCE_HISTORY\n')

    def test_global_session_lease_protects_original_history_across_manager_registries(self):
        other=Manager(self.provider,self.config,self.base/'other-manager')
        other.connect_profile('source',self.source);other.connect_profile('target',self.target)
        saved=remember(other,'source',self.sid,cwd=self.base)
        owner='fixture-global-history-owner'
        acquire_session(self.manager,owner,self.sid,self.source)
        try:
            with self.assertRaisesRegex(Error,'active session'):
                continue_session(other,saved['ref'],profile='target')
        finally:release(self.manager,owner)
        self.assertEqual(self.path.read_text(),'SOURCE_HISTORY\n')
        self.assertEqual(continue_session(other,saved['ref'],profile='target'),0)

    def test_preexisting_destination_conversation_is_not_replaced(self):
        if self.provider=='claude':path=self.target/'projects/local-project'/(self.sid+'.jsonl')
        else:path=self.target/'session-state'/self.sid/'events.jsonl'
        path.parent.mkdir(parents=True);path.write_text('KEEP_LOCAL')
        with self.assertRaisesRegex(Error,'destination'):self.resume()
        self.assertEqual(path.read_text(),'KEEP_LOCAL');self.assertEqual(self.path.read_text(),'SOURCE_HISTORY\n')

    def test_fresh_cli_session_is_retained_for_later_cross_environment_continue(self):
        with patch('agent_manager.runtime.execution.current_directory',return_value=self.base):
            self.assertEqual(self.manager._run_native('source',[]),0)
        row=self.observed('source');saved=for_resume(self.manager,'source',row['id'])
        self.assertIsNotNone(saved)
        self.assertEqual(continue_session(self.manager,saved['ref'],profile='target'),0)
        self.assertEqual(self.observed('target')['id'],row['id'])

    def test_native_resume_argv_in_destination_resolves_original_route(self):
        self.assertEqual(self.resume(),0)
        self.assertEqual(self.manager._run_native('target',['--resume',self.sid]),0)
        self.assertEqual(self.observed('target')['history'],str(self.path))
        self.assertEqual(self.observed('target')['cwd'],str(self.base))
        self.assertEqual(len(listing(self.manager,refresh=False)),1)

    def test_route_cannot_be_rebound_to_a_different_origin_with_the_same_uuid(self):
        self.view()
        third=self.manager.home('third')
        path=third/'projects/project'/(self.sid+'.jsonl') if self.provider=='claude' else third/'session-state'/self.sid/'events.jsonl'
        path.parent.mkdir(parents=True);path.write_text('OTHER_HISTORY')
        if self.provider=='copilot':(path.parent/'workspace.yaml').write_text('cwd: '+str(self.base))
        saved=remember(self.manager,'third',self.sid,cwd=self.base)
        with self.assertRaisesRegex(Error,'another history store'):
            continue_session(self.manager,saved['ref'],profile='target')
        self.assertEqual(self.path.read_text(),'SOURCE_HISTORY\n')

    def test_fresh_identity_cannot_replace_retained_history_or_a_route(self):
        with self.assertRaisesRegex(Error,'use continue'):
            self.manager._run_native('source',['--session-id',self.sid])
        self.view()
        with self.assertRaisesRegex(Error,'use continue'):
            self.manager._run_native('target',['--session-id',self.sid])
        self.assertEqual(self.path.read_text(),'SOURCE_HISTORY\n')

    def test_queued_receipt_revalidates_history_before_submission(self):
        from agent_manager.runtime.names import _submission
        view=self.view()
        state=self.manager.state();state['runs']['prepared']=dict(agent='target',environment_id=self.manager.resolve_profile('target').environment_id,
            runtime='undercurrent',status='prepared',home=str(self.target),thread_id=self.sid,source_history_home=str(self.source),
            native_continuation=view.receipt,launch={'command':['never-run']})
        self.manager.save(state)
        self.path.rename(self.path.with_name('moved-history'))
        with self.assertRaises(Error):_submission(self.manager,'prepared',set())
        self.assertEqual(self.manager.state()['runs']['prepared']['status'],'prepared')

    def test_failed_registry_publication_creates_no_session_link(self):
        with patch.object(self.manager,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.view()
        self.assertNotIn('session_routes',self.manager.state())
        self.assertFalse((self.target/'session-state'/self.sid).exists())

    def test_connected_native_history_can_be_discovered_and_continued(self):
        state=self.manager.state();state['sessions'].clear();self.manager.save(state)
        if self.provider=='claude':
            self.path.write_text(json.dumps(dict(cwd=str(self.base),sessionId=self.sid,message='PRIVATE_BODY'))+'\n')
        else:
            (self.path.parent/'workspace.yaml').write_text('id: '+self.sid+'\ncwd: '+json.dumps(str(self.base))+'\n')
        saved=for_resume(self.manager,'source',self.sid)
        self.assertEqual(saved['cwd'],str(self.base))
        self.assertNotIn('PRIVATE_BODY',json.dumps(self.manager.state()))
        self.assertEqual(continue_session(self.manager,saved['ref'],profile='target'),0)
        self.assertEqual(self.observed('target')['history'],str(self.path))
        self.assertEqual([row['ref'] for row in listing(self.manager)],[saved['ref']])

    def test_discovery_preserves_captured_workspace_and_resources(self):
        if self.provider=='claude':
            self.path.write_text(json.dumps(dict(cwd=str(self.base/'changed')))+'\n')
        else:
            (self.path.parent/'workspace.yaml').write_text('cwd: '+str(self.base/'changed'))
        self.assertEqual(listing(self.manager),[self.saved])

    def test_queued_receipt_rejects_a_changed_source_reference(self):
        from agent_manager.runtime.native.file_continuation import validate_receipt
        view=self.view()
        state=self.manager.state();state['sessions'][self.saved['ref']]['cwd']=str(self.base/'changed');self.manager.save(state)
        with self.assertRaisesRegex(Error,'source session reference changed'):
            validate_receipt(self.manager,view.receipt)


@unittest.skipUnless(sys.platform=='linux','native file handoffs currently require Linux')
class ClaudeContinuationTests(Fixture,unittest.TestCase):
    provider='claude'

    def test_native_session_pid_marker_blocks_handoff(self):
        folder=self.source/'sessions';folder.mkdir()
        (folder/(str(os.getpid())+'.json')).write_text(json.dumps(dict(sessionId=self.sid)))
        with self.assertRaisesRegex(Error,'native Claude process'):self.resume()
        self.assertNotIn('session_routes',self.manager.state())

    def test_discovery_skips_ambiguous_and_escaping_transcripts(self):
        state=self.manager.state();state['sessions'].clear();self.manager.save(state)
        self.path.write_text(json.dumps(dict(cwd=str(self.base)))+'\n')
        duplicate=self.source/'projects/duplicate'/self.path.name
        duplicate.parent.mkdir();duplicate.write_text(self.path.read_text())
        self.assertEqual(listing(self.manager),[])
        duplicate.unlink()
        outside=self.base/'outside';self.path.parent.rename(outside)
        self.path.parent.symlink_to(outside,target_is_directory=True)
        self.assertEqual(listing(self.manager),[])


@unittest.skipUnless(sys.platform=='linux','native file handoffs currently require Linux')
class CopilotContinuationTests(Fixture,unittest.TestCase):
    provider='copilot'

    def test_only_selected_session_is_linked_and_lost_owned_link_can_be_recreated(self):
        view=self.view();link=self.target/'session-state'/self.sid
        self.assertTrue(link.is_symlink());self.assertEqual(link.resolve(),self.path.parent)
        self.assertFalse((self.target/'config.json').exists());self.assertFalse((self.target/'providers.json').exists())
        link.unlink();self.assertEqual(self.resume(),0);self.assertTrue(link.is_symlink())

    def test_untracked_symlink_is_not_adopted(self):
        link=self.target/'session-state'/self.sid;link.parent.mkdir();link.symlink_to(self.path.parent,target_is_directory=True)
        with self.assertRaisesRegex(Error,'occupied'):self.resume()
        self.assertTrue(link.is_symlink())

    def test_discovery_parses_quoted_workspace_and_skips_wrong_identity(self):
        from agent_manager.runtime.native.copilot_sessions import discover
        workspace=self.path.parent/'workspace.yaml'
        cwd=str(self.base/"has 'quotes' and : colon")
        workspace.write_text('id: '+self.sid+"\ncwd: '"+cwd.replace("'","''")+"'\n")
        self.assertEqual(discover(self.source)[0]['cwd'],cwd)
        workspace.write_text('id: '+str(uuid.uuid4())+'\ncwd: '+json.dumps(cwd)+'\n')
        self.assertEqual(discover(self.source),[])

    def test_replaced_link_is_rejected_before_queued_launch_submission(self):
        view=self.view();link=self.target/'session-state'/self.sid
        link.unlink();link.mkdir()
        with self.assertRaisesRegex(Error,'link changed'):view.command(self.manager,[str(self.cli),'--resume',self.sid])

    def test_wrapper_termination_waits_for_native_child_and_releases_history_lock(self):
        from agent_manager.storage.platform import check_process
        view=self.view();command=view.command(self.manager,[str(self.cli),'--resume',self.sid])
        process=subprocess.Popen(command,cwd=self.base,env=self.manager.launch_environment(self.target)|{'FIXTURE_SLEEP':'30','FIXTURE_IGNORE_TERM':'1'},
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+5
            while not (self.target/'observed.json').exists():
                if time.monotonic()>deadline:self.fail('Native child did not start')
                time.sleep(.01)
            pid=self.observed('target')['pid']
            process.terminate();self.assertEqual(process.wait(timeout=8),143)
            with self.assertRaises(ProcessLookupError):check_process(pid)
            self.assertEqual(self.resume(),0)
        finally:
            if process.poll() is None:process.kill();process.wait()


@unittest.skipUnless(sys.platform=='linux','native file handoffs currently require Linux')
class ManagedContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_continue_and_raw_resume_keep_original_history_and_workspace(self):
        for provider in ('claude','copilot'):
            fixture=type('Disposable',(Fixture,unittest.TestCase),{'provider':provider})()
            fixture.setUp()
            try:
                manager=fixture.manager
                client=Client(provider,config=fixture.config,root=fixture.base/'manager',runtime=FakeRuntime())
                def accepted(owner,run_id):
                    row=read_run(owner,run_id)
                    result=subprocess.run(row['launch']['command'],cwd=row['launch']['cwd'],
                        env=os.environ|row['launch']['env'],capture_output=True,text=True)
                    if result.returncode:raise AssertionError(result.stderr)
                    update_run(owner,run_id,status='running',session_id='fixture-'+run_id)
                with patch('agent_manager.interfaces.sdk.launch_worker',side_effect=accepted):
                    first=await client.continue_session(fixture.saved['ref'],profile='target')
                    self.assertEqual(fixture.observed('target')['history'],str(fixture.path))
                    row=read_run(manager,first.run_id)
                    self.assertEqual(row['source_history_home'],str(fixture.source))
                    self.assertEqual(row['launch']['cwd'],str(fixture.base))
                    finish_run(manager,first.run_id,status='finished')
                    second=await client.spawn(profile='target',resume_id=fixture.sid)
                    self.assertEqual(fixture.observed('target')['id'],fixture.sid)
                    finish_run(manager,second.run_id,status='finished')
            finally:fixture.doCleanups()

    async def test_automatic_continuation_uses_eligible_profile_and_recovers_without_preparing_again(self):
        from agent_manager.profiles.selection import Decision
        from agent_manager.environments.operations import revision
        fixture = type('Disposable', (Fixture, unittest.TestCase), {'provider': 'claude'})()
        fixture.setUp()
        try:
            manager = fixture.manager
            client = Client('claude', config=fixture.config, root=fixture.base / 'manager', runtime=FakeRuntime())
            from unittest.mock import AsyncMock
            client.reconcile = AsyncMock(return_value=[])
            target = manager.resolve_profile('target')
            decision = Decision(target, revision(manager.state(), target.environment_id), 'Fixture eligible selection')
            key = str(uuid.uuid4())
            def accepted(owner, run_id):
                row = read_run(owner, run_id)
                result = subprocess.run(row['launch']['command'], cwd=row['launch']['cwd'],
                    env=os.environ | row['launch']['env'], capture_output=True, text=True)
                if result.returncode: raise AssertionError(result.stderr)
                update_run(owner, run_id, status='running', session_id='fixture-' + run_id)
            with patch('agent_manager.profiles.selection.choose', return_value=decision) as choose, \
                    patch('agent_manager.interfaces.sdk.launch_worker', side_effect=accepted) as worker:
                agent = await client.continue_session(fixture.saved['ref'], auto=True, idempotency_key=key)
            self.assertIn(target.environment_id, choose.call_args.kwargs['environment_ids'])
            self.assertEqual(agent.profile, 'target')
            self.assertEqual(agent.selection.environment_id, target.environment_id)
            self.assertEqual(fixture.observed('target')['history'], str(fixture.path))
            self.assertEqual(fixture.observed('target')['account'], 'target')
            worker.assert_called_once()
            with patch('agent_manager.runtime.continuation.coordinator.resolve_request', side_effect=AssertionError('No repeat preparation')):
                recovered = await client.continue_session(fixture.saved['ref'], auto=True, idempotency_key=key)
            self.assertEqual(recovered.reference, agent.reference)
            finish_run(manager, agent.run_id, status='finished')
        finally: fixture.doCleanups()
