import os
from pathlib import Path
import sys
import tempfile
import unittest

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    HAS_MCP = True
except ImportError:
    HAS_MCP = False


@unittest.skipUnless(HAS_MCP, "Install aixand-agent-manager[mcp] for protocol tests")
class ManagementMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_standard_stdio_profile_lifecycle_and_structured_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'providers.toml'
            config.write_text('')
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-B", "-m", "agent_manager.interfaces.management_mcp",
                    "--home", directory, "--config", str(config)],
                env={
                    key: os.environ[key]
                    for key in ("PATH", "PYTHONPATH")
                    if key in os.environ
                },
            )
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    initialized = await session.initialize()
                    self.assertEqual(initialized.serverInfo.name, "aixand_agents")
                    tools = await session.list_tools()
                    names = {tool.name for tool in tools.tools}
                    self.assertIn("create_profile", names)
                    self.assertIn('get_budget', names)
                    self.assertIn('list_budgets', names)
                    self.assertNotIn(
                        "start_login", names
                    )  # Login stays with the provider CLI.
                    created = await session.call_tool(
                        "create_profile", {"provider": "codex", "name": "work"}
                    )
                    self.assertFalse(created.isError)
                    profile = created.structuredContent["profile"]
                    inspected = await session.call_tool(
                        "get_profile", {"provider": "codex", "profile": "work"}
                    )
                    self.assertEqual(
                        inspected.structuredContent["profile"]["profile_id"],
                        profile["profile_id"],
                    )
                    observation = await session.call_tool('get_budget',
                        {'provider': 'codex', 'profile': 'work', 'cached': True})
                    self.assertFalse(observation.isError)
                    self.assertEqual(observation.structuredContent['budget']['environment_id'], profile['environment_id'])
                    self.assertEqual(observation.structuredContent['budget']['status'], 'unknown')
                    invalid = await session.call_tool(
                        "delete_profile",
                        {"provider": "codex", "profile": "work", "password": "unsupported"},
                    )
                    self.assertTrue(invalid.isError)
                    deleted = await session.call_tool(
                        "delete_profile",
                        {
                            "provider": "codex",
                            "profile": "work",
                            "expectedRevision": profile["revision"],
                        },
                    )
                    self.assertTrue(deleted.structuredContent["deleted"])
                    self.assertTrue(Path(profile["home"]).is_dir())
