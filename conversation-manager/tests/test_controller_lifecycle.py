"""Controller lifecycle survives acknowledgement gaps without native process ownership."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from conversation_manager import ControllerSpec, NativeController, ConversationError


class Owner:
    identity = {'runtime': 'fixture', 'home': '/fixture'}
    def __init__(self):
        self.states = {'native': 'running'}
        self.matches, self.starts, self.stops = [], 0, []
        self.lose_start = False
        self.allow_stop = True
    async def inspect(self, identity): return self.states.get(identity, 'missing')
    async def find(self, epoch, attempt): return list(self.matches)
    async def start(self, command, **options):
        self.starts += 1
        self.matches = ['controller']
        self.states['controller'] = 'running'
        if self.lose_start: raise OSError('Lost process acknowledgement')
        return 'controller'
    async def stop(self, identity):
        self.stops.append(identity)
        if self.allow_stop: self.states[identity] = 'exited'
        return self.allow_stop


class ControllerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.spec = ControllerSpec('epoch', '/upstream', '/frontend', '/control', str(self.root), 'native-server', 'native')
        self.owner, self.controller = Owner(), NativeController(self.root / 'server')

    async def test_lost_start_is_adopted_after_reopening_and_a_name_change(self):
        self.owner.lose_start = True
        with self.assertRaises(OSError): await self.controller.ensure(self.spec, self.owner)
        state = await NativeController(self.root / 'server').ensure(replace(self.spec, name='renamed'), self.owner)
        self.assertEqual(state['process_id'], 'controller')
        self.assertEqual(self.owner.starts, 1)
        self.assertEqual(self.owner.stops, [])

    async def test_unknown_launch_blocks_replacement_and_retirement(self):
        self.owner.lose_start = True
        with self.assertRaises(OSError): await self.controller.ensure(self.spec, self.owner)
        self.owner.matches = []
        with self.assertRaises(ConversationError): await self.controller.ensure(self.spec, self.owner)
        self.assertFalse(await self.controller.retire(self.spec, self.owner))
        self.assertEqual(self.owner.starts, 1)

    async def test_retirement_requires_termination_and_never_stops_the_native_process(self):
        await self.controller.ensure(self.spec, self.owner)
        self.owner.allow_stop = False
        self.assertFalse(await self.controller.retire(self.spec, self.owner))
        self.owner.allow_stop = True
        self.assertTrue(await self.controller.retire(self.spec, self.owner))
        self.assertEqual(self.owner.states['native'], 'running')
        self.assertEqual(set(self.owner.stops), {'controller'})

    async def test_changed_transport_cannot_retarget_existing_controller(self):
        await self.controller.ensure(self.spec, self.owner)
        with self.assertRaises(ConversationError):
            await self.controller.ensure(replace(self.spec, upstream='/other'), self.owner)
        self.assertEqual(self.owner.starts, 1)
