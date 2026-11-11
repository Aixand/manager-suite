import asyncio
import tempfile
import unittest

from conversation_manager import Client, ConversationError, PreparedLaunch, StandaloneLauncher


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.client = Client(self.temporary.name)
        self.launcher = StandaloneLauncher(self.temporary.name,
            service_factory='conversation_manager.fixture_sdk:factory')
        launch = await self.client.launch(PreparedLaunch(
            backend_id='claude', adapter_id='claude-sdk', protocol='claude-agent-sdk',
            argv=[], cwd=self.temporary.name, transport={'kind': 'sdk'}),
            launcher=self.launcher, operation_id='launch')
        self.assertEqual(launch['status'], 'completed', launch)
        self.reference = launch['result']['connection_ref']
        self.connection = await self.client.connect(self.reference)

    async def asyncTearDown(self):
        await self.connection.disconnect()
        await self.launcher.terminate(self.reference['runtime_ref'], operation_id='stop')
        self.temporary.cleanup()

    async def test_conversation_survives_frontend_disconnect(self):
        conversation = await self.connection.create_conversation(operation_id='create')
        reference = conversation.reference
        reference['native_session_id'] = 'modified-copy'
        self.assertNotEqual(reference, conversation.reference)
        sent = await conversation.send('slow', operation_id='send')
        await self.connection.disconnect()
        self.connection = await Client(self.temporary.name).connect(self.reference)
        restored = await self.connection.attach_conversation(conversation.reference)
        async with asyncio.timeout(10):
            async for event in restored.events():
                if event['kind'] == 'turn.completed':
                    break
        result = await restored.turn_result(sent['result']['turn_id'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(restored.reference, conversation.reference)
        self.assertIn(conversation.reference, await self.client.conversations())
        self.assertTrue((await restored.history())['entries'])
        self.assertTrue((await restored.commands())['commands'])
        loaded = await self.connection.load_conversation(conversation.reference, operation_id='load')
        self.assertEqual(loaded.reference, conversation.reference)

    async def test_failed_creation_preserves_operation_evidence(self):
        with self.assertRaises(ConversationError) as caught:
            await self.connection.create_conversation({'unknown-setting': True}, operation_id='invalid')
        self.assertEqual(caught.exception.operation_id, 'invalid')
        self.assertEqual(caught.exception.recovery['receipt']['status'], 'unsupported')

    async def test_acp_is_rejected_before_launch(self):
        with self.assertRaises(ConversationError) as caught:
            PreparedLaunch(backend_id='fixture', adapter_id='acp', protocol='acp',
                argv=['unused'], cwd=self.temporary.name).to_dict()
        self.assertEqual(caught.exception.code, 'unsupported')
