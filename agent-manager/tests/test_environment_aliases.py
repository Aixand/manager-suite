"""Alias churn must never redirect pending or active native work."""
import asyncio
import contextlib
import copy
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from agent_manager import Error
from agent_manager.profiles.operations import inspect_profile, duplicate_profile, delete_profile, Management
from agent_manager.runtime.execution import launch_home
from agent_manager.runtime.runs import read_run, update_run
from agent_manager.runtime.maintenance import validate_operation, perform
from agent_manager.storage.locks import locks
from tests.support import ProfileFixture, ManagedFixture


class AliasTests(ProfileFixture, unittest.TestCase):
    def setUp(self):
        super().setUp(); self.create()

    def test_managed_native_home_uses_the_dedicated_home_tree(self):
        target = self.manager.resolve_profile('work')
        self.assertEqual(self.manager.managed_homes, self.base / 'homes/home/test')
        self.assertEqual(self.manager.home(target), self.manager.managed_homes / target.environment_id)
        self.assertEqual(self.manager.profile_directory(target), self.manager.home(target))
        self.assertFalse((self.manager.root / 'environments').exists())

    def test_aliases_share_home_preferences_login_and_environment_revision(self):
        original = inspect_profile(self.manager, 'work')
        alias = self.manager.alias_profile('w', 'work')
        self.assertNotEqual(alias['profile_id'], original['profile_id'])
        self.assertEqual(alias['environment_id'], original['environment_id'])
        self.assertEqual(alias['home'], original['home'])
        self.manager.set_model('w', 'model')
        updated = inspect_profile(self.manager, 'work')
        self.assertEqual(updated['model'], 'model')
        self.assertEqual(updated['revision'], original['revision'])
        self.assertNotEqual(updated['environment_revision'], original['environment_revision'])
        self.assertEqual(updated['login'], inspect_profile(self.manager, 'w')['login'])
        self.manager.set_launch_arguments(['--yolo'], 'w')
        self.assertEqual(self.manager.saved_launch_args('work'), self.manager.saved_launch_args('w'))

    def test_connect_reuses_environment_and_unnamed_environment_can_be_reattached(self):
        original = inspect_profile(self.manager, 'work')
        connected = self.manager.connect_profile('second', original['home'])
        self.assertEqual(connected['environment_id'], original['environment_id'])
        delete_profile(self.manager, 'work'); delete_profile(self.manager, 'second')
        self.assertEqual(self.manager.environments()[0]['profiles'], [])
        reattached = self.manager.alias_profile('back', environment_id=original['environment_id'])
        self.assertEqual(reattached['home'], original['home'])
        self.assertEqual(len(self.manager.state()['environments']), 1)

    def test_default_uses_alias_uuid_and_clears_only_when_selected_alias_is_deleted(self):
        self.manager.alias_profile('w', 'work'); self.manager.set_default('work')
        self.manager.rename('work', 'company')
        self.assertEqual(self.manager.resolve_profile(), 'company')
        delete_profile(self.manager, 'w'); self.assertEqual(self.manager.resolve_profile(), 'company')
        delete_profile(self.manager, 'company')
        self.assertIsNone(self.manager.state()['default_profile_id'])

    def test_pending_launch_claim_survives_rename_delete_and_name_reuse(self):
        target = self.manager.resolve_profile('work'); home = self.manager.home(target)
        before = inspect_profile(self.manager, 'work')['environment_revision']
        with launch_home(self.manager, target) as plan:
            self.manager.rename('work', 'company')
            delete_profile(self.manager, 'company'); self.manager.new_profile('work')
            plan.claim(self.manager, self.manager.state(), target)
            self.assertEqual(self.manager.home(target), home)
            self.assertNotEqual(self.manager.home('work'), home)
            from agent_manager.environments.operations import revision
            self.assertEqual(revision(self.manager.state(), target.environment_id), before)

    def test_running_native_process_allows_alias_churn_and_keeps_original_lease(self):
        original = self.manager.resolve_profile('work'); home = self.manager.home(original)
        errors = []
        def run():
            try: self.assertEqual(self.manager._run_native('work', []), 0)
            except BaseException as exc: errors.append(exc)
        with patch.dict(os.environ, FAKE_RUN_SLEEP='.6'):
            worker = threading.Thread(target=run); worker.start()
            try:
                deadline = time.monotonic() + 4
                while not (home / 'observed.json').exists():
                    if time.monotonic() > deadline: self.fail('Disposable provider did not start')
                    time.sleep(.01)
                self.manager.rename('work', 'company'); self.manager.alias_profile('c', 'company')
                delete_profile(self.manager, 'company'); self.manager.new_profile('work')
                self.assertTrue(worker.is_alive())
                self.assertEqual(len(self.manager.active_runs(self.manager.state(), 'c')), 1)
                self.assertEqual(self.manager.active_runs(self.manager.state(), 'work'), [])
                with self.assertRaisesRegex(Error, 'Stop this profile'): self.manager.login('c')
                delete_profile(self.manager, 'c')
                with self.assertRaisesRegex(Error, 'Active'): self.manager.forget_environment(original.environment_id)
            finally: worker.join(timeout=4)
        self.assertFalse(worker.is_alive()); self.assertEqual(errors, [])
        self.assertEqual(json.loads((home / 'observed.json').read_text())['home'], str(home))

    def test_login_captures_environment_before_waiting_and_status_never_updates_reused_name(self):
        original = self.manager.resolve_profile('work'); home = self.manager.home(original)
        @contextlib.contextmanager
        def rename_on_lock(root, *keys, **options):
            if any(key.startswith('environment-') for key in keys):
                self.manager.rename('work', 'company'); self.manager.new_profile('work')
            with locks(root, *keys, **options): yield
        with patch('agent_manager.environments.login.locks', side_effect=rename_on_lock), patch.object(self.manager, '_invoke_native', return_value=0) as invoke:
            self.manager.login('work')
        self.assertEqual(invoke.call_args.args[0], home)
        self.assertNotIn(self.manager.resolve_profile('work').environment_id, self.manager.state()['login_status'])
        from dataclasses import replace
        spec = replace(self.manager.provider_spec, login=replace(self.manager.provider_spec.login, status_args=('status',)))
        def native(*args, **kwargs):
            self.manager.rename('company', 'renamed'); delete_profile(self.manager, 'renamed')
            self.manager.new_profile('company')
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        with patch.object(self.manager, 'provider_spec', spec), patch('agent_manager.environments.login.subprocess.run', side_effect=native):
            self.manager.login_status(original)
        self.assertIn(original.environment_id, self.manager.state()['login_status'])
        self.assertNotIn(self.manager.resolve_profile('company').environment_id, self.manager.state()['login_status'])

    def test_duplicate_has_one_publish_and_failure_leaves_no_alias_or_home(self):
        self.manager.set_model('work', 'saved')
        original = copy.deepcopy(self.manager.state())
        with patch.object(self.manager, 'save', side_effect=OSError('disk failure')) as save:
            with self.assertRaises(OSError): duplicate_profile(self.manager, 'work', 'copy')
        save.assert_called_once(); self.assertEqual(self.manager.state(), original)
        self.assertEqual(len(list(self.manager.managed_homes.iterdir())), 1)
        with patch.object(self.manager, 'save', wraps=self.manager.save) as save:
            result = duplicate_profile(self.manager, 'work', 'copy')
        save.assert_called_once(); self.assertEqual(result['model'], 'saved')
        self.assertEqual(result['login']['verification'], 'unchecked')

    def test_revisions_are_separate_and_all_dispatchers_validate(self):
        row = inspect_profile(self.manager, 'work')
        self.manager.rename('work', 'company', expected_revision=row['revision'])
        self.assertEqual(inspect_profile(self.manager, 'company')['environment_revision'], row['environment_revision'])
        with self.assertRaisesRegex(Error, 'alias changed'): delete_profile(self.manager, 'company', expected_revision=row['revision'])
        self.manager.set_model('company', 'changed')
        with patch.object(self.manager, '_invoke_native') as invoke:
            with self.assertRaisesRegex(Error, 'Environment changed'): self.manager.login('company', expected_revision=row['environment_revision'])
        invoke.assert_not_called()
        management = Management(self.manager)
        for tool, args in [('create_profile', {}), ('rename_profile', {'profile': 'company', 'newName': 'new'}), ('delete_profile', {'profile': 'company', 'purge': True}), ('alias_profile', {'name': 'n'})]:
            with self.subTest(tool=tool), self.assertRaises(Error): management.execute(tool, args)

    def test_environment_retirement_is_separate_and_cannot_redirect_old_targets(self):
        target = self.manager.resolve_profile('work'); home = self.manager.home(target)
        with self.assertRaisesRegex(Error, 'aliases'): self.manager.purge_environment(target.environment_id)
        delete_profile(self.manager, 'work'); self.manager.purge_environment(target.environment_id)
        self.assertFalse(home.exists()); self.manager.new_profile('work')
        with self.assertRaisesRegex(Error, 'original environment'): self.manager.home(target)

    def test_cli_alias_environment_and_logout_revision_contract(self):
        import io
        from dataclasses import replace
        from agent_manager.interfaces.cli.dispatch import execute
        prefix = ['--config', str(self.config), '--home', str(self.base / 'homes'), 'test']
        identifier = self.manager.resolve_profile('work').environment_id
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(execute([*prefix, 'profile', 'alias', 'w', 'work']), 0)
            self.assertEqual(execute([*prefix, 'environment', 'show', identifier]), 0)
            self.assertEqual(execute([*prefix, 'profile', 'delete', 'w']), 0)
        spec = replace(self.manager.provider_spec, login=replace(self.manager.provider_spec.login, logout_args=('logout',)))
        with patch('agent_manager.interfaces.cli.dispatch.Manager', return_value=self.manager), patch.object(self.manager, 'provider_spec', spec), patch.object(self.manager, '_invoke_native') as invoke:
            with self.assertRaisesRegex(Error, 'Environment changed'):
                execute([*prefix, 'logout', 'work', '--expected-revision', 'sha256:' + '0' * 64])
        invoke.assert_not_called()


    def test_queued_login_remains_pinned_after_alias_removal_and_reuse(self):
        target = self.manager.resolve_profile('work')
        spec = dict(kind='profile-login', provider='test', config=str(self.config), home=str(self.base / 'homes'), profile='work', expectedRevision=inspect_profile(self.manager, 'work')['environment_revision'])
        validate_operation(self.manager, spec)
        delete_profile(self.manager, 'work'); self.manager.new_profile('work')
        with patch('agent_manager.manager.Manager._invoke_native', return_value=0) as invoke: perform(spec)
        self.assertEqual(invoke.call_args.args[0], self.manager.home(target))


class ManagedAliasTests(ManagedFixture, unittest.IsolatedAsyncioTestCase):
    async def test_sdk_and_mcp_expose_same_alias_and_environment_operations(self):
        from agent_manager.interfaces.management_mcp import LocalBackend
        self.create(); client = self.client()
        alias = await client.alias_profile('w', 'work')
        result = await LocalBackend(config=self.config, home=self.base / 'homes').execute('alias_profile',
            dict(provider='test', name='company', environmentId=alias['environment_id']), str(uuid.uuid4()))
        self.assertEqual(result['profile']['environment_id'], alias['environment_id'])
        self.assertEqual(len(await client.environments()), 1)
        self.assertEqual(len((await client.get_environment(alias['environment_id']))['profiles']), 3)
        with self.assertRaises(Error): await client.manage_profiles('rename_profile', {'profile': 'work', 'newName': 'missingRevision'})
        await client.delete_profile('w')

    async def test_sdk_prepare_and_reconnect_follow_environment_after_alias_churn(self):
        self.create(); target = self.manager.resolve_profile('work'); client = self.client()
        launch_args = self.manager.launch_args
        changed = False
        def rename_during_prepare(*args, **kwargs):
            nonlocal changed
            if not changed:
                changed = True
                self.manager.rename('work', 'company'); delete_profile(self.manager, 'company')
                self.manager.new_profile('work')
            return launch_args(*args, **kwargs)
        def accepted(manager, run_id): update_run(manager, run_id, session_id='fixture', status='running')
        with patch.object(client.manager, 'launch_args', side_effect=rename_during_prepare), patch('agent_manager.interfaces.sdk.launch_worker', side_effect=accepted):
            agent = await client.spawn(profile='work')
        self.assertEqual(agent.environment_id, target.environment_id)
        self.assertEqual((await client.get(agent.run_id)).environment_id, target.environment_id)
        row = read_run(self.manager, agent.run_id)
        self.assertEqual(row['environment_id'], target.environment_id)
        self.assertEqual(self.manager.home(row['agent']), self.manager.home(target))
        from agent_manager.runtime.runs import finish_run
        finish_run(self.manager, agent.run_id, status='finished')
