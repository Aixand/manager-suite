"""Live adapter handshakes enforce provider policy before creating sessions."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from conversation_manager import PreparedLaunch, ConversationError
from conversation_manager.adapters.codex import CodexAdapter
from conversation_manager.adapters.copilot_sdk import CopilotDriver
from conversation_manager.adapters.claude_sdk import ClaudeDriver


class RuntimePolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_claude_checks_its_cli_before_starting_an_sdk_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            cli = Path(temporary) / 'claude'
            cli.write_text('#!/bin/sh\nprintf "3.0.0 (Claude Code)\\n"\n')
            cli.chmod(0o755)
            driver = ClaudeDriver({'cwd': temporary}, 'fixture-session', Path(temporary), False, SimpleNamespace(),
                {'HOME': temporary, 'CLAUDE_CONFIG_DIR': temporary, 'AIXAND_CLAUDE_CLI': str(cli)}, authentication={'method': 'profile-login'})
            with patch.dict('sys.modules', {'claude_agent_sdk': SimpleNamespace()}):
                with self.assertRaises(ConversationError) as error:
                    await driver.start()
                self.assertEqual(error.exception.code, 'unsupported')
                self.assertIn('3.0.0', str(error.exception))

    async def test_codex_checks_the_actual_initialization_version(self):
        for version, accepted in [('0.164.0', True), ('0.164.1', True), ('0.167.0', False), ('fixture', False)]:
            with self.subTest(version=version):
                wire = SimpleNamespace(connect=AsyncMock(), close=AsyncMock(), _socket=object(),
                    initialization={'userAgent': 'codex_cli_rs/' + version})
                adapter = CodexAdapter(SimpleNamespace(operation_names=[]), PreparedLaunch(
                    backend_id='codex', adapter_id='native-codex', protocol='codex-app-server', argv=[],
                    cwd='/workspace', transport={'kind': 'unix-websocket', 'endpoint': '/fixture/socket'}))
                with patch('conversation_manager.native_codex.NativeCodexConnection', return_value=wire):
                    if accepted:
                        await adapter.start()
                        self.assertTrue(adapter.connected)
                        wire.close.assert_not_awaited()
                    else:
                        with self.assertRaises(ConversationError) as error:
                            await adapter.start()
                        self.assertEqual(error.exception.code, 'unsupported')
                        self.assertFalse(adapter.connected)
                        wire.close.assert_awaited_once()

    async def test_copilot_accepts_compatible_updates_and_still_checks_auth_and_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / 'copilot'
            executable.write_text('fixture')
            for version, accepted in [('1.0.103', True), ('1.0.104', True), ('2.0.0', False), ('unknown', False)]:
                with self.subTest(version=version):
                    client = SimpleNamespace(start=AsyncMock(), get_status=AsyncMock(return_value={'version': version}))
                    module = SimpleNamespace(CopilotClient=Mock(return_value=client),
                        RuntimeConnection=SimpleNamespace(for_stdio=Mock(return_value='fixture-transport')))
                    driver = CopilotDriver({'cwd': temporary}, 'session', Path(temporary), False, SimpleNamespace(),
                        {'AIXAND_COPILOT_CLI': str(executable)}, authentication={'method': 'copilot-login'})
                    driver.check_account, driver.catalog = AsyncMock(), AsyncMock()
                    with patch.dict('sys.modules', {'copilot': module}):
                        if accepted:
                            await driver.connect()
                            driver.check_account.assert_awaited_once()
                            driver.catalog.assert_awaited_once()
                        else:
                            with self.assertRaises(ConversationError) as error:
                                await driver.connect()
                            self.assertEqual(error.exception.code, 'unsupported')
                            driver.check_account.assert_not_awaited()
                            driver.catalog.assert_not_awaited()
