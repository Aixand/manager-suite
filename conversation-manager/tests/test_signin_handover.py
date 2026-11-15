import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from conversation_manager import signin
from conversation_manager.core import Core
from conversation_manager.models import ConversationError


class SigninStopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.records = {}
        self.queue = []
        self.thread = dict(status=dict(type='idle'))
        self.goal = None
        self.processes = []
        self.native_queue = []
        self.core = SimpleNamespace(
            ref=dict(connection_id='owner'), epoch='epoch', active={}, inflight=set(), requests={},
            sessions={'s': dict(session_id='s')}, draining=False, lost=False,
            message_queue=SimpleNamespace(get=lambda session: dict(items=self.queue)),
            store=SimpleNamespace(get=lambda kind, key: copy.deepcopy(self.records.get((kind, key))),
                put=lambda kind, key, value: self.records.__setitem__((kind, key), copy.deepcopy(value))),
            adapter=SimpleNamespace(controls={}, rpc=AsyncMock(side_effect=self.rpc)))

    async def rpc(self, method, params):
        return {
            'remoteControl/status/read': dict(status='disabled'),
            'thread/loaded/list': dict(data=['parent', 'native-child'], nextCursor=None),
            'thread/read': dict(thread=self.thread),
            'thread/backgroundTerminals/list': dict(data=self.processes, nextCursor=None),
            'thread/queue/list': dict(data=self.native_queue, nextCursor=None),
            'thread/goal/get': dict(goal=self.goal),
        }[method]

    async def prepare(self):
        return await Core.dispatch(self.core, 'prepare_signin_stop', dict(operation_id='signin'))

    async def test_idle_worker_seals_admission_and_repeats_safely(self):
        self.assertTrue((await self.prepare())['ready'])
        self.assertTrue(self.core.draining)
        self.assertTrue((await self.prepare())['ready'])
        await Core.dispatch(self.core, 'cancel_signin_stop', dict(operation_id='signin'))
        self.assertFalse(self.core.draining)

    async def test_active_turn_and_approvals_are_never_drained(self):
        for field, value in [('active', {'turn': {}}), ('requests', {'r': dict(record=dict(state='submitted'))}),
                             ('inflight', {'send'})]:
            original = getattr(self.core, field)
            setattr(self.core, field, value)
            self.assertFalse((await self.prepare())['ready'])
            self.assertFalse(self.core.draining)
            setattr(self.core, field, original)
        self.core.adapter.rpc.assert_not_awaited()

    async def test_readonly_preflight_does_not_change_admission_or_storage(self):
        self.assertTrue((await Core.dispatch(self.core, 'signin_activity', {}))['ready'])
        self.assertFalse(self.core.draining)
        self.assertEqual(self.records, {})

    async def test_paused_and_uncertain_queued_messages_block_stop(self):
        for status in ['pending', 'dispatching', 'uncertain', 'failed']:
            self.queue = [dict(status=status)]
            self.assertFalse((await self.prepare())['ready'])
        self.queue = [dict(status='delivered'), dict(status='removed')]
        self.assertTrue((await self.prepare())['ready'])

    async def test_native_children_commands_goals_and_queues_block_stop(self):
        for attr, value in [('thread', dict(status=dict(type='active'))),
                            ('thread', dict(status=dict(type='unknown'))),
                            ('thread', dict(status=dict(type='idle'), ephemeral=True)),
                            ('processes', [dict(processId='running')]),
                            ('native_queue', [dict(id='queued')]),
                            ('goal', dict(status='active'))]:
            original = getattr(self, attr)
            setattr(self, attr, value)
            result = await self.prepare()
            self.assertFalse(result['ready'], (attr, result))
            self.assertFalse(self.core.draining)
            setattr(self, attr, original)

    async def test_unsupported_or_incomplete_observations_fail_closed(self):
        self.core.adapter.rpc.side_effect = RuntimeError('unsupported')
        result = await self.prepare()
        self.assertIn(dict(kind='activity_unavailable'), result['blockers'])
        self.assertFalse(self.core.draining)

    async def test_turn_racing_with_inspection_prevents_stop_and_reopens_admission(self):
        async def raced(method, params):
            self.core.active['new-turn'] = {}
            return await self.rpc(method, params)
        self.core.adapter.rpc.side_effect = raced
        self.assertFalse((await self.prepare())['ready'])
        self.assertFalse(self.core.draining)

    async def test_cancelled_inspection_reopens_admission(self):
        self.core.adapter.rpc.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.prepare()
        self.assertFalse(self.core.draining)

    async def test_cannot_release_another_maintenance_owner(self):
        await self.prepare()
        with self.assertRaises(ConversationError):
            signin.cancel(self.core, 'other')
        self.assertTrue(self.core.draining)

    async def test_opencode_inbox_permissions_and_background_processes_are_not_idle(self):
        responses = {
            '/api/session/active': dict(data=[]),
            '/api/session?limit=100': dict(data=[dict(id='native')]),
            '/api/session?parentID=native&limit=100': dict(data=[]),
            '/api/session/native/inbox': dict(data=[]),
            '/api/session/native/permission': dict(data=[]),
            '/api/session/native/form': dict(data=[]),
            '/api/experimental/session/native/terminal': dict(data=[]),
        }
        async def http(method, path):
            return responses[path]
        self.core.adapter = SimpleNamespace(http=AsyncMock(side_effect=http))
        for path in ['/api/session/native/inbox', '/api/session/native/permission', '/api/session/native/form']:
            responses[path]['data'] = [dict(id='pending')]
            self.assertFalse((await self.prepare())['ready'])
            self.assertFalse(self.core.draining)
            responses[path]['data'] = []
        terminal = '/api/experimental/session/native/terminal'
        responses[terminal]['data'] = [dict(id='process', sessionID='native', command='work', cwd='/fixture', status='running')]
        self.assertFalse((await self.prepare())['ready'])
        responses[terminal]['data'] = []
        self.assertTrue((await self.prepare())['ready'])

    async def test_opencode_missing_activity_api_preserves_worker(self):
        self.core.adapter = SimpleNamespace(http=AsyncMock(side_effect=RuntimeError('unsupported')))
        result = await self.prepare()
        self.assertFalse(result['ready'])
        self.assertFalse(self.core.draining)
