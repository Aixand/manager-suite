"""List free OpenCode models, or create and launch a profile with a chosen model."""
import argparse
import asyncio

import agent_manager
import undercurrent


async def main(args):
    async with undercurrent.Client() as runtime:
        async with agent_manager.Client("opencode", runtime=runtime) as agents:
            if args.model is None:
                models = await agents.models(free=True, cwd=args.cwd)
                for model in models:
                    print(model["id"])
                if not models:
                    print("No free models reported for this project.")
                return

            await agents.create_profile(args.profile, model=args.model)
            agent = await agents.spawn(profile=args.profile, cwd=args.cwd)
            print("Run:", agent.run_id)
            print("Session:", agent.session_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Model ID to launch; omit to list free models")
    parser.add_argument("--profile", default="sdk-demo", help="Unused profile name (default: sdk-demo)")
    parser.add_argument("--cwd", default=".", help="Project directory (default: current directory)")
    asyncio.run(main(parser.parse_args()))
