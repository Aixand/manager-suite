"""Standalone local Agent Manager administration over stdio MCP."""

import argparse
import asyncio
import json
import uuid

from agent_manager.errors import Error
from agent_manager.manager import Manager
from agent_manager._version import __version__
from agent_manager.profiles.operations import Management
from agent_manager.interfaces.management_contract import INSTRUCTIONS, LOGIN_TOOLS, NAMESPACE, TOOLS, validate


class LocalBackend:
    def __init__(self, *, config=None, home=None):
        self.config, self.home = config, home

    def providers(self):
        from agent_manager.providers.config import load
        return list(load(self.config)[0])

    async def execute(self, tool_name, arguments, request_id):
        import shutil

        validate(tool_name, arguments)
        if tool_name in LOGIN_TOOLS:
            raise Error(
                "Interactive login requires the agent login CLI"
            )
        if tool_name == "list_providers":
            rows = []
            for provider in self.providers():
                manager = Manager(provider, self.config, self.home)
                rows.append(
                    {
                        "provider": manager.provider,
                        "installed": shutil.which(manager.config["executable"])
                        is not None,
                        **manager.provider_spec.capabilities(),
                        "loginMethods": [],
                    }
                )
            return {"providers": rows}
        provider = arguments.get("provider")
        if tool_name == "list_profiles" and not provider:
            rows = []
            for item in self.providers():
                result = await self.execute(
                    tool_name, {**arguments, "provider": item}, request_id
                )
                rows += result["profiles"]
            return {"profiles": rows}
        manager = Manager(provider, self.config, self.home)
        values = {
            key: value
            for key, value in arguments.items()
            if key not in ("provider", "requestId", "query")
        }
        result = await asyncio.to_thread(
            Management(manager).execute,
            tool_name,
            values,
            request_id=arguments.get("requestId") or request_id,
        )
        if tool_name == "list_profiles" and arguments.get("query"):
            query = arguments["query"].casefold()
            result["profiles"] = [
                row for row in result["profiles"] if query in row["profile"].casefold()
            ]
        return result


async def serve(backend):
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import CallToolResult, TextContent, Tool

    server = Server(NAMESPACE, version=__version__, instructions=INSTRUCTIONS)
    session = uuid.uuid4()

    @server.list_tools()
    async def listing():
        return [
            Tool(**row)
            for row in TOOLS
            if row["name"] not in LOGIN_TOOLS
        ]

    @server.call_tool()
    async def call(tool_name, arguments):
        try:
            identifier = str(
                uuid.uuid5(session, str(server.request_context.request_id))
            )
            result = await backend.execute(tool_name, arguments or {}, identifier)
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(result))],
                structuredContent=result,
                isError=result.get("status") in ("uncertain", "failed"),
            )
        except (Error, ValueError, OSError) as error:
            return CallToolResult(
                content=[TextContent(type="text", text=str(error)[:1000])], isError=True
            )

    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description="Agent Manager profile MCP (stdio)")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--home")
    parser.add_argument("--config")
    args = parser.parse_args()
    backend = LocalBackend(config=args.config, home=args.home)
    try:
        asyncio.run(serve(backend))
    except ImportError:
        parser.exit(2, "Install aixand-agent-manager[mcp] to run the MCP server\n")


if __name__ == "__main__":
    main()
