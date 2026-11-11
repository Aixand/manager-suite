"""Launch an existing profile and leave its session running."""
import argparse
import asyncio

import agent_manager
import undercurrent


async def main(args):
    async with undercurrent.Client() as runtime:
        async with agent_manager.Client(args.provider, runtime=runtime) as agents:
            agent = await agents.spawn(profile=args.profile, cwd=args.cwd)
            print("Run:", agent.run_id)
            print("Session:", agent.session_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("codex", "opencode", "claude", "copilot", "antigravity", "agy"), default="codex")
    parser.add_argument("--profile", default="research", help="Existing profile (default: research)")
    parser.add_argument("--cwd", default=".", help="Project directory (default: current directory)")
    asyncio.run(main(parser.parse_args()))
