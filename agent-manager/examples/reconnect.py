"""Read a managed session by run ID, or explicitly stop it."""
import argparse
import asyncio

import agent_manager
import undercurrent


async def main(args):
    async with undercurrent.Client() as runtime:
        async with agent_manager.Client(args.provider, runtime=runtime) as agents:
            agent = await agents.get(args.run_id)
            if args.stop:
                await runtime.stop(agent.session_id)
                print("Stopped:", agent.session_id)
            else:
                state = await runtime.inspect(agent.session_id)
                print("State:", state.lifecycle)
                screen = await runtime.read_screen(agent.session_id)
                print(screen.decode("utf-8", "replace"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", help="Run ID printed by the launch example")
    parser.add_argument("--provider", choices=("codex", "opencode", "claude", "copilot", "antigravity", "agy"), default="codex")
    parser.add_argument("--stop", action="store_true", help="Stop the session instead of reading it")
    asyncio.run(main(parser.parse_args()))
