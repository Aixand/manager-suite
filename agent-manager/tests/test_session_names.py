import asyncio
from itertools import cycle
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from tests import support as fixtures
from tests.support import FakeRuntime
from agent_manager import Client, Error
from agent_manager.runtime import names as session_names
from agent_manager.runtime.runs import finish_run, read_run, update_run
from agent_manager.runtime.supervisor import supervise


class NameAllocationTests(unittest.TestCase):
    def test_word_lists_provide_readable_combinations(self):
        for words, count in ((session_names.ADJECTIVES, 256), (session_names.NOUNS, 512)):
            self.assertEqual(len(words), count)
            self.assertEqual(len(set(words)), count)
            for word in words:
                self.assertRegex(word, r'^[a-z]+$')

    def test_reservation_skips_saved_active_and_daemon_names(self):
        state = {'session_names': {'work-calm-otter': 'retired'},
                 'runs': {'active': {'launch': {'name': 'work-brisk-fox'}}}}
        with patch.object(session_names.secrets, 'choice', side_effect=[
            'calm', 'otter', 'brisk', 'fox', 'quiet', 'pine', 'clever', 'heron',
        ]):
            result = session_names.allocate_name(state, 'work', 'new', {'work-quiet-pine'})
        self.assertEqual(result, 'work-clever-heron')
        self.assertEqual(state['session_names'][result], 'new')
        self.assertEqual(state['session_names']['work-calm-otter'], 'retired')

    def test_profile_prefix_is_preserved_within_runtime_length_limit(self):
        with patch.object(session_names.secrets, 'choice', side_effect=cycle(['calm', 'otter'])):
            self.assertEqual(session_names.allocate_name({}, 'Work_team-2', 'one'), 'Work_team-2-calm-otter')
            long = session_names.allocate_name({}, 'p' * 128, 'two')
        self.assertEqual(len(long), 128)
        self.assertTrue(long.endswith('-calm-otter'))

    def test_repeated_collisions_fail_with_a_bounded_error(self):
        state = {'session_names': {'work-calm-otter': 'old'}}
        with patch.object(session_names.secrets, 'choice', side_effect=cycle(['calm', 'otter'])):
            with self.assertRaisesRegex(Error, 'unused session name'):
                session_names.allocate_name(state, 'work', 'new')
        self.assertEqual(state['session_names'], {'work-calm-otter': 'old'})


class RuntimeError(Exception):
    category = 'ServerRejected'

    def __init__(self, *, product_category=None, effect_certainty=None):
        super().__init__('runtime rejected or lost the request')
        self.product_category = product_category
        self.effect_certainty = effect_certainty


class ManagedNameTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ProfileFixture.setUp
    create = fixtures.ProfileFixture.create

    def client(self):
        return Client('test', config=self.config, root=self.base / 'homes', runtime=FakeRuntime())

    async def launch(self, **options):
        def accepted(manager, run_id):
            if read_run(manager, run_id)['status'] == 'prepared':
                update_run(manager, run_id, session_id='session-' + run_id, status='running')
        with patch('agent_manager.interfaces.sdk.launch_worker', side_effect=accepted):
            return await self.client().spawn(**{'profile': 'work', **options})

    async def prepare(self):
        self.create()
        with patch.object(session_names.secrets, 'choice', side_effect=['calm', 'otter']):
            agent = await self.launch()
        update_run(self.manager, agent.run_id, status='prepared', session_id=None)
        return agent.run_id

    def runtime(self, *, occupied=(), errors=()):
        session = SimpleNamespace(id='accepted')
        return SimpleNamespace(list=AsyncMock(return_value=[SimpleNamespace(name=name) for name in occupied]),
            start=AsyncMock(side_effect=[*errors, session]), wait=AsyncMock())

    async def test_keyed_retries_keep_name_and_internal_identity(self):
        self.create()
        key = str(uuid.uuid4())
        with patch.object(session_names.secrets, 'choice', side_effect=['calm', 'otter']) as choose:
            first = await self.launch(idempotency_key=key)
            second = await self.launch(idempotency_key=key)
        self.assertEqual(first.run_id, uuid.UUID(key).hex)
        self.assertEqual(first, second)
        self.assertEqual(choose.call_count, 2)
        row = read_run(self.manager, first.run_id)
        self.assertEqual(row['launch']['name'], 'work-calm-otter')
        self.assertEqual((await self.client().runs())[0]['name'], 'work-calm-otter')

    async def test_default_selection_supplies_the_actual_profile(self):
        self.create()
        self.manager.set_default('work')
        agent = await self.launch(profile=None)
        self.assertRegex(read_run(self.manager, agent.run_id)['launch']['name'], r'^work-[a-z]+-[a-z]+$')

    async def test_concurrent_launches_reserve_distinct_names(self):
        self.create()
        def accepted(manager, run_id):
            if read_run(manager, run_id)['status'] == 'prepared':
                update_run(manager, run_id, session_id='session-' + run_id, status='running')
        with patch.object(session_names.secrets, 'choice', side_effect=[
            'calm', 'otter', 'calm', 'otter', 'brisk', 'heron',
        ]), patch('agent_manager.interfaces.sdk.launch_worker', side_effect=accepted):
            first, second = await asyncio.gather(*(self.client().spawn(profile='work') for _ in range(2)))
        names = {read_run(self.manager, agent.run_id)['launch']['name'] for agent in (first, second)}
        self.assertEqual(names, {'work-calm-otter', 'work-brisk-heron'})

    async def test_finalized_name_stays_reserved_for_later_clients(self):
        self.create()
        with patch.object(session_names.secrets, 'choice', side_effect=[
            'calm', 'otter', 'calm', 'otter', 'quiet', 'pine',
        ]):
            first = await self.launch()
            finish_run(self.manager, first.run_id, status='finished')
            second = await self.launch()
        self.assertEqual(read_run(self.manager, first.run_id)['launch']['name'], 'work-calm-otter')
        self.assertEqual(read_run(self.manager, second.run_id)['launch']['name'], 'work-quiet-pine')

    async def test_daemon_collision_is_resolved_before_submission(self):
        run_id = await self.prepare()
        runtime = self.runtime(occupied=['work-calm-otter'])
        with patch.object(session_names.secrets, 'choice', side_effect=['brisk', 'heron']):
            await session_names.start(self.manager, run_id, runtime)
        launch = read_run(self.manager, run_id)['launch']
        self.assertEqual(launch['name'], 'work-brisk-heron')
        self.assertEqual(launch['idempotency_key'], run_id)
        runtime.start.assert_awaited_once_with(**launch)

    async def test_creation_race_retries_only_a_proven_absent_name_conflict(self):
        run_id = await self.prepare()
        runtime = self.runtime(errors=[RuntimeError(product_category='name_conflict', effect_certainty='known_absent')])
        with patch.object(session_names.secrets, 'choice', side_effect=['quiet', 'pine']):
            await session_names.start(self.manager, run_id, runtime)
        first, second = (call.kwargs for call in runtime.start.await_args_list)
        self.assertEqual(first['name'], 'work-calm-otter')
        self.assertEqual(second['name'], 'work-quiet-pine')
        self.assertNotEqual(first['idempotency_key'], second['idempotency_key'])
        self.assertEqual(second, read_run(self.manager, run_id)['launch'])
        self.assertEqual(second['labels']['agent_manager.run_id'], run_id)

    async def test_uncertain_replies_keep_the_name_and_do_not_submit_again(self):
        self.create()
        for category, certainty in [('name_conflict', None), (None, None), ('capacity_exceeded', 'known_absent')]:
            with self.subTest(category=category, certainty=certainty):
                agent = await self.launch()
                run_id = agent.run_id
                update_run(self.manager, run_id, status='prepared', session_id=None)
                runtime = self.runtime(errors=[RuntimeError(product_category=category, effect_certainty=certainty)])
                before = read_run(self.manager, run_id)['launch']
                with self.assertRaises(RuntimeError):
                    await session_names.start(self.manager, run_id, runtime)
                self.assertEqual(read_run(self.manager, run_id)['launch'], before)
                self.assertEqual(runtime.start.await_count, 1)
                with self.assertRaisesRegex(Error, 'without replay'):
                    await session_names.start(self.manager, run_id, runtime)
                self.assertEqual(runtime.start.await_count, 1)

    async def test_prepared_names_are_submitted_unchanged(self):
        run_id = await self.prepare()
        state = self.manager.state()
        row = state['runs'][run_id]
        row.pop('runtime_name')
        row['launch']['name'] = 'agent-' + run_id
        self.manager.save(state)
        runtime = self.runtime()
        await session_names.start(self.manager, run_id, runtime)
        runtime.list.assert_not_awaited()
        self.assertEqual(runtime.start.await_args.kwargs['name'], 'agent-' + run_id)

    async def test_supervisor_recovers_a_lost_reply_by_run_label_with_the_saved_name(self):
        run_id = await self.prepare()
        runtime = self.runtime(errors=[RuntimeError()])
        context = AsyncMock()
        context.__aenter__.return_value = runtime
        module = SimpleNamespace(Client=lambda **_: context, UndercurrentError=RuntimeError)
        with patch.dict('sys.modules', undercurrent=module):
            with self.assertRaises(RuntimeError):
                await supervise(self.manager, run_id)
            self.assertEqual(read_run(self.manager, run_id)['status'], 'uncertain')
            runtime.list.return_value = [SimpleNamespace(id='recovered')]
            await supervise(self.manager, run_id)
        runtime.start.assert_awaited_once()
        runtime.wait.assert_awaited_once_with('recovered', lifecycle=('stopped', 'exited', 'failed'), timeout=300)
        self.assertEqual(read_run(self.manager, run_id)['launch']['name'], 'work-calm-otter')

    async def test_failed_read_only_name_check_keeps_launch_prepared(self):
        run_id = await self.prepare()
        runtime = self.runtime()
        runtime.list.side_effect = [[], RuntimeError()]
        context = AsyncMock()
        context.__aenter__.return_value = runtime
        module = SimpleNamespace(Client=lambda **_: context, UndercurrentError=RuntimeError)
        with patch.dict('sys.modules', undercurrent=module):
            with self.assertRaises(RuntimeError):
                await supervise(self.manager, run_id)
        runtime.start.assert_not_awaited()
        self.assertEqual(read_run(self.manager, run_id)['status'], 'prepared')
