from __future__ import annotations
import asyncio
import tempfile

from . import Client, PreparedLaunch, StandaloneLauncher


async def demo(home=None):
    temporary = tempfile.TemporaryDirectory(prefix="conversation-manager-demo-") if home is None else None
    home = home or temporary.name
    client, launcher = Client(home), StandaloneLauncher(home, service_factory="conversation_manager.fixture_sdk:factory")
    launch = await client.launch(PreparedLaunch(backend_id="claude", adapter_id="claude-sdk", protocol="claude-agent-sdk",
        argv=[], cwd=home, transport={"kind": "sdk"}), launcher=launcher, operation_id="demo-launch")
    if launch["status"] != "completed":
        return launch
    ref = launch["result"]["connection_ref"]
    connection = await client.connect(ref)
    try:
        conversation = await connection.create_conversation(operation_id="demo-create")
        session = conversation.reference
        sent = await conversation.send("slow", operation_id="demo-send")
        await connection.disconnect()
        # A second independent frontend attaches while the retained turn runs.
        second = await Client(home).connect(ref)
        async with asyncio.timeout(10):
            async for event in second.events(session):
                if event["kind"] == "turn.completed":
                    break
        await connection.reconnect()
        page = await connection.event_page(session)
        await second.disconnect()
        return {"status": "completed", "connection_ref": ref, "conversation_ref": session,
            "send_receipt": sent, "completion": await client.operation("demo-send"),
            "reattached_same_runtime": connection.ref == ref,
            "events": page["events"], "capabilities": await connection.capabilities()}
    finally:
        await connection.disconnect()
        await launcher.terminate(ref["runtime_ref"], operation_id="demo-terminate")
        if temporary:
            temporary.cleanup()
