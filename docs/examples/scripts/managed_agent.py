"""Launch, recover, inspect, message, and stop a retained native agent execution.

Requires current Agent Manager + Undercurrent SDKs. Carrier is optional.
Closing this utility detaches; stop is explicit. No provider login is performed.
"""

import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import uuid

from agent_manager import Client, LaunchError
from undercurrent import Client as Runtime


def write_json(path, value, *, exclusive=False):
    text = json.dumps(value, indent=2) + "\n"
    if exclusive:
        with path.open("x", encoding="utf-8") as stream:
            os.chmod(path, 0o600)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        return
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def absolute(value):
    return str(value.expanduser().resolve()) if value else None


async def main(args):
    state = args.state.expanduser().resolve()
    if args.action == "launch":
        project = args.project.expanduser().resolve()
        if not project.is_dir():
            raise ValueError("Project directory must exist")
        if args.carrier_db and args.provider != "codex":
            raise ValueError("The Carrier adapter currently supports Codex only")
        runtime = Runtime(home=args.runtime_home, bridge=args.bridge)
        intent = {
            "operation_id": str(uuid.uuid4()),
            "provider": args.provider,
            "profile": args.profile,
            "cwd": str(project),
            "config": absolute(args.config),
            "root": absolute(args.root),
            "runtime": runtime.connection_options,
            "detection": not args.no_detection,
            "carrier": {
                "database": absolute(args.carrier_db),
                "url": args.carrier_url,
                "home": str(state / "carrier"),
            }
            if args.carrier_db
            else None,
        }
        state.mkdir(parents=True, mode=0o700, exist_ok=False)
        write_json(state / "intent.json", intent, exclusive=True)
    else:
        intent = json.loads((state / "intent.json").read_text())
        runtime = Runtime(**intent["runtime"])

    async with runtime:
        async with Client(
            intent["provider"],
            runtime=runtime,
            config=intent["config"],
            root=intent["root"],
        ) as agents:
            adapter = None
            if intent["carrier"]:
                from carrier.integrations.agent_manager import CarrierAgents

                adapter = CarrierAgents(agents, **intent["carrier"])
            if args.action in ("launch", "retry-launch"):
                launcher = adapter or agents
                try:
                    agent = await launcher.spawn(
                        profile=intent["profile"],
                        cwd=intent["cwd"],
                        idempotency_key=intent["operation_id"],
                        detection=intent["detection"],
                    )
                except LaunchError as error:
                    write_json(state / "launch-error.json", {"run_id": error.run_id})
                    raise
                write_json(state / "reference.json", agent.reference.to_dict())
            else:
                if not (state / "reference.json").exists():
                    raise ValueError(
                        "No execution reference; inspect the saved launch intent and use retry-launch"
                    )
                agent = await agents.get(
                    json.loads((state / "reference.json").read_text())
                )

            if args.action == "send":
                if not args.message_file:
                    raise ValueError("send requires --message-file")
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", args.input_id):
                    raise ValueError(
                        "input-id must use 1–64 lowercase letters, digits, or hyphens"
                    )
                content = args.message_file.read_text(encoding="utf-8")
                path = state / f"input-{args.input_id}.json"
                if path.exists():
                    delivery = json.loads(path.read_text())
                    if delivery["text"] != content:
                        raise ValueError(
                            "Input ID already has different text; inspect the original intent"
                        )
                else:
                    delivery = {"text": content, "delivery_id": str(uuid.uuid4())}
                    write_json(path, delivery, exclusive=True)
                await agent.wait(
                    lifecycle="running", interaction="available", timeout=args.timeout
                )
                receipt = await agent.send_input(**delivery)
                write_json(state / f"receipt-{args.input_id}.json", asdict(receipt))
                print(json.dumps({"input_receipt": asdict(receipt)}))
            elif args.action == "stop":
                await agent.stop()
                await agent.wait(
                    lifecycle=("stopped", "exited", "failed"), timeout=args.timeout
                )
            elif args.action == "screen":
                print((await agent.read_screen()).decode("utf-8", "replace"))
                return

            observation = {"execution": (await agent.inspect()).to_dict()}
            if adapter:
                observation["carrier"] = await adapter.inspect(intent["operation_id"])
            print(json.dumps(observation, indent=2))
            # Context exit closes connections, leaving execution alive unless stopped.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("launch", "retry-launch", "inspect", "send", "screen", "stop"),
    )
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--provider", choices=("codex",), default="codex")
    parser.add_argument("--profile", default="main")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--runtime-home", type=Path)
    parser.add_argument("--bridge")
    parser.add_argument(
        "--no-detection",
        action="store_true",
        help="For fixture qualification; send requires observable readiness",
    )
    parser.add_argument("--carrier-db", type=Path)
    parser.add_argument("--carrier-url", default="http://127.0.0.1:8877")
    parser.add_argument("--message-file", type=Path)
    parser.add_argument("--input-id", default="task")
    parser.add_argument("--timeout", type=float, default=60)
    options = parser.parse_args()
    if options.timeout <= 0:
        parser.error("timeout must be positive")
    asyncio.run(main(options))
