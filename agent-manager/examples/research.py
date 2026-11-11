"""Send prompts to Codex, print its terminal, and stop the session."""
import argparse
import asyncio

import agent_manager
import undercurrent


async def main(args):
    async with undercurrent.Client() as runtime:
        async with agent_manager.Client("codex", runtime=runtime) as agents:
            agent = await agents.spawn(profile=args.profile, cwd=args.cwd)
            print(f"Run: {agent.run_id}; Session: {agent.session_id}", flush=True)
            try:
                await runtime.wait_for_stability(
                    agent.session_id, lifecycle="running", interaction="available",
                    stable_for=2, timeout=120,
                )
                prompts = args.prompt or [
                    "Explain this project's structure. Do not change files.",
                    "Summarize your findings in three bullets.",
                ]
                for prompt in prompts:
                    receipt = await runtime.send_input(agent.session_id, prompt)
                    # Wait for observations at or after this input's revision.
                    await runtime.wait_for_stability(
                        agent.session_id, lifecycle="running",
                        interaction="available", stable_for=2, timeout=300,
                        after_revision=receipt.foreground_revision,
                    )
                    screen = await runtime.read_screen(agent.session_id)
                    print(screen.decode("utf-8", "replace"), flush=True)
            finally:
                await runtime.stop(agent.session_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="research", help="Existing Codex profile (default: research)")
    parser.add_argument("--cwd", default=".", help="Project directory (default: current directory)")
    parser.add_argument("--prompt", action="append", help="Prompt to send; repeat for follow-ups")
    asyncio.run(main(parser.parse_args()))
