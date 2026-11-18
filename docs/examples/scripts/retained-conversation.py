"""Exercise retained conversation behavior with the built-in offline fixture."""
import asyncio
import json

from conversation_manager.demo import demo


async def main():
    result = await demo()
    assert result["status"] == "completed"
    assert result["reattached_same_runtime"] is True
    assert any(event["kind"] == "turn.completed" for event in result["events"])
    print(json.dumps({"status": result["status"], "reattached_same_runtime": True,
                      "turn_completed": True, "provider": "injected-fixture"}))


if __name__ == "__main__":
    asyncio.run(main())
