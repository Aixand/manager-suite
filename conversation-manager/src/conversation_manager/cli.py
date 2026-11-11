"""Contract CLI. JSON descriptors accept inline JSON or @PATH."""

from __future__ import annotations
import argparse
import asyncio
import importlib
from pathlib import Path
import sys
import os
import re

from .client import Client
from . import __version__
from .models import canonical, decode, redact, ConversationError


def json_arg(text):
    try:
        return decode(Path(text[1:]).read_bytes() if text.startswith("@") else text)
    except OSError as exc:
        raise ConversationError("io", "Cannot read JSON input file") from exc


def factory(spec, options):
    try:
        module, name = spec.split(":", 1)
        return getattr(importlib.import_module(module), name)(options)
    except (ValueError, ImportError, AttributeError, TypeError) as exc:
        raise ConversationError(
            "validation", "Invalid explicit MODULE:factory configuration"
        ) from exc


def parser():
    p = argparse.ArgumentParser(prog="conversation-manager")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--home")
    p.add_argument("--config", help="Editable Conversation Manager TOML defaults")
    p.add_argument(
        "--runtime",
        dest="runtime_kind",
        choices=("standalone", "undercurrent"),
        default=None,
    )
    p.add_argument(
        "--runtime-options", default=None, help="Daemon connection JSON or @PATH"
    )
    sub = p.add_subparsers(dest="command", required=True)
    config_parser = sub.add_parser("config").add_subparsers(
        dest="config_action", required=True
    )
    for action in ("init", "show", "path"):
        config_parser.add_parser(action).add_argument("--json", action="store_true")

    def common(q, connection=True):
        q.add_argument("--json", action="store_true")
        q.add_argument("--options", default="{}")
        if connection:
            q.add_argument("--connection", required=True)
            q.add_argument("--connector")

    q = sub.add_parser("launch")
    common(q, False)
    q.add_argument(
        "--process",
        default="{}",
        help="Worker name, labels and metadata as JSON or @PATH",
    )
    q.add_argument("--prepared", required=True)
    q.add_argument("--operation-id", required=True)
    q.add_argument("--launcher")
    q.add_argument(
        "--env-vars",
        default="",
        help="Explicit comma-separated environment names to pass transiently",
    )
    codex = sub.add_parser("codex").add_subparsers(dest="codex_action", required=True)
    q = codex.add_parser("launch")
    q.add_argument("--process", default="{}")
    q.add_argument("--environment-id", required=True)
    q.add_argument("--cwd", required=True)
    q.add_argument("--agent-config")
    q.add_argument("--agent-root")
    q.add_argument("--settings", default="{}")
    q.add_argument("--operation-id", required=True)
    q.add_argument("--json", action="store_true")
    q = sub.add_parser("agents")
    q.add_argument("--agent-config")
    q.add_argument("--agent-root")
    q.add_argument("--provider", action="append")
    q.add_argument("--json", action="store_true")
    for command in ("connect", "reconnect", "capabilities"):
        common(sub.add_parser(command))
    q = sub.add_parser("operation")
    q.add_argument("operation_id")
    common(q, False)
    q = sub.add_parser("demo")
    q.add_argument("--fixture", action="store_true", required=True)
    q.add_argument("--json", action="store_true")
    q = sub.add_parser("terminate")
    common(q, False)
    q.add_argument("--runtime", required=True)
    q.add_argument("--operation-id", required=True)
    q.add_argument("--launcher")
    sessions = sub.add_parser("conversation").add_subparsers(
        dest="action", required=True
    )
    q = sessions.add_parser("start")
    q.add_argument("--process", default="{}")
    q.add_argument("--agent", required=True)
    q.add_argument("--cwd", required=True)
    q.add_argument("--settings", default="{}")
    q.add_argument("--operation-id", required=True)
    q.add_argument("--json", action="store_true")
    q = sessions.add_parser("attach")
    q.add_argument("--conversation", dest="session", required=True)
    q.add_argument("--json", action="store_true")
    q = sessions.add_parser("list")
    q.add_argument("--detailed", action="store_true")
    q.add_argument("--json", action="store_true")
    q = sessions.add_parser("stop-startup")
    q.add_argument("--startup-operation-id", required=True)
    q.add_argument("--operation-id", required=True)
    q.add_argument("--json", action="store_true")
    for command in (
        "create",
        "load",
        "restore",
        "send",
        "events",
        "history",
        "respond",
        "cancel",
        "pending",
        "state",
        "result",
        "commands",
        "slash-commands",
        "command",
        "configuration",
        "configure",
        "steer",
        "close",
    ):
        q = sessions.add_parser(command)
        common(q)
        if command != "create":
            q.add_argument("--conversation", dest="session", required=True)
        if command not in (
            "events",
            "pending",
            "history",
            "state",
            "result",
            "commands",
            "slash-commands",
            "configuration",
        ):
            q.add_argument("--operation-id", required=True)
        if command == "create":
            q.add_argument("--settings", default="{}")
        if command == "restore":
            q.add_argument("--launcher")
        if command == "history":
            q.add_argument("--cursor", type=int, default=0)
            q.add_argument("--limit", type=int, default=100)
        if command == "send":
            q.add_argument("--content", required=True)
        if command == "events":
            q.add_argument("--after-sequence", type=int, default=0)
            q.add_argument("--follow", action="store_true")
        if command == "respond":
            q.add_argument("--request-id", required=True)
            q.add_argument("--response", required=True)
        if command == "cancel":
            q.add_argument("--turn-id", required=True)
        if command == "result":
            q.add_argument("--turn-id", required=True)
        if command == "slash-commands":
            q.add_argument("--available-only", action="store_true")
        if command == "command":
            q.add_argument("--name", required=True)
            q.add_argument("--arguments", default="")
        if command == "configure":
            q.add_argument("--name", required=True)
            q.add_argument("--value", required=True)
        if command == "steer":
            q.add_argument("--text", required=True)
            q.add_argument("--expected-turn-id", required=True)
    return p


async def run(args):
    if args.command == "config":
        from .config import initialize, load, path, runtime_defaults, state_home

        if args.config_action == "init":
            return initialize(args.config)
        if args.config_action == "path":
            return {"path": str(path(args.config))}
        settings = load(args.config)
        kind, options = runtime_defaults(
            settings,
            args.runtime_kind,
            json_arg(args.runtime_options)
            if args.runtime_options is not None
            else None,
        )
        return {
            **settings,
            "home": str(state_home(args.home, settings=settings)),
            "runtime": kind,
            "runtime_options": options,
        }
    client = Client(
        args.home,
        agent_config=getattr(args, "agent_config", None),
        agent_root=getattr(args, "agent_root", None),
        runtime=args.runtime_kind,
        config=args.config,
        runtime_options=json_arg(args.runtime_options)
        if args.runtime_options is not None
        else None,
    )
    if args.command == "agents":
        return await client.available_agents(
            **({"providers": args.provider} if args.provider else {})
        )
    if args.command == "conversation" and args.action in (
        "start",
        "attach",
        "list",
        "stop-startup",
    ):
        if args.action == "list":
            return await client.conversations(detailed=args.detailed)
        if args.action == "stop-startup":
            return await client.stop_startup(
                args.startup_operation_id, operation_id=args.operation_id
            )
        conversation = (
            await client.start_conversation(
                agent=json_arg(args.agent),
                cwd=args.cwd,
                settings=json_arg(args.settings),
                operation_id=args.operation_id,
                process=json_arg(args.process),
            )
            if args.action == "start"
            else await client.attach_conversation(json_arg(args.session))
        )
        try:
            return dict(
                conversation_ref=conversation.reference,
                startup_receipt=conversation.startup_receipt,
                state=await conversation.state(),
            )
        finally:
            await conversation.disconnect()
    if args.command == "codex":
        from .agents import AgentEnvironments

        agents = AgentEnvironments(
            "codex", config=args.agent_config, root=args.agent_root
        )
        return await client.launch_codex(
            args.environment_id,
            cwd=args.cwd,
            agents=agents,
            settings=json_arg(args.settings),
            operation_id=args.operation_id,
            process=json_arg(args.process),
        )
    if args.command == "demo":
        if client.runtime["kind"] != "standalone":
            raise ConversationError(
                "unsupported",
                "The fixture demo uses standalone ownership; use conversation start or launch for Undercurrent",
            )
        from .demo import demo

        return await demo(args.home)
    if args.command == "operation":
        return await client.operation(args.operation_id)
    if args.command in ("launch", "terminate"):
        launcher = (
            factory(args.launcher, json_arg(args.options)) if args.launcher else None
        )
        if args.command == "launch":
            from .models import PreparedLaunch

            names = [name.strip() for name in args.env_vars.split(",") if name.strip()]
            if any(
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                or name not in os.environ
                for name in names
            ):
                raise ConversationError(
                    "validation",
                    "Requested transient environment names must be valid and present",
                )
            prepared = PreparedLaunch.from_dict(
                json_arg(args.prepared), env={name: os.environ[name] for name in names}
            )
            return await client.launch(
                prepared,
                launcher=launcher,
                operation_id=args.operation_id,
                process=json_arg(args.process),
            )
        if launcher is None:
            return await client.terminate_runtime(
                json_arg(args.runtime), operation_id=args.operation_id
            )
        return await launcher.terminate(
            json_arg(args.runtime), operation_id=args.operation_id
        )
    connector = (
        factory(args.connector, json_arg(args.options)) if args.connector else None
    )
    connection = await client.connect(json_arg(args.connection), connector=connector)
    try:
        if args.command in ("connect", "reconnect"):
            if args.command == "reconnect":
                await connection.reconnect()
            return connection.ref
        if args.command == "capabilities":
            return await connection.capabilities()
        session = json_arg(args.session) if getattr(args, "session", None) else None
        if args.action == "create":
            return await connection.create_session(
                json_arg(args.settings), operation_id=args.operation_id
            )
        if args.action == "state":
            return await connection.session_state(session)
        if args.action == "result":
            return await connection.turn_result(session, args.turn_id)
        if args.action == "commands":
            return await connection.commands(session)
        if args.action == "slash-commands":
            return await connection.list_slash_commands(
                session, available_only=args.available_only
            )
        if args.action == "configuration":
            return await connection.configuration(session)
        if args.action == "configure":
            return await connection.set_config(
                session, args.name, json_arg(args.value), operation_id=args.operation_id
            )
        if args.action == "command":
            return await connection.command(
                session, args.name, args.arguments, operation_id=args.operation_id
            )
        if args.action == "steer":
            return await connection.steer(
                session,
                args.text,
                expected_turn_id=args.expected_turn_id,
                operation_id=args.operation_id,
            )
        if args.action == "close":
            return await connection.close_session(
                session, operation_id=args.operation_id
            )
        if args.action == "load":
            return await connection.load_session(
                session, operation_id=args.operation_id
            )
        if args.action == "restore":
            owner = (
                factory(args.launcher, json_arg(args.options))
                if args.launcher
                else None
            )
            return await connection.restore_session(
                session, launcher=owner, operation_id=args.operation_id
            )
        if args.action == "history":
            return await connection.history_page(
                session, cursor=args.cursor, limit=args.limit
            )
        if args.action == "send":
            return await connection.send(
                session, json_arg(args.content), operation_id=args.operation_id
            )
        if args.action == "pending":
            return await connection.pending_requests(session)
        if args.action == "respond":
            pending = await connection.pending_requests(session)
            if not any(r["request_id"] == args.request_id for r in pending):
                raise ConversationError(
                    "conflict",
                    "Request does not belong to supplied session or is no longer pending",
                )
            return await connection.respond(
                args.request_id, json_arg(args.response), operation_id=args.operation_id
            )
        if args.action == "cancel":
            return await connection.cancel(
                session, args.turn_id, operation_id=args.operation_id
            )
        if args.action == "events":
            if args.follow:
                async for event in connection.events(
                    session, after_sequence=args.after_sequence
                ):
                    print(canonical(redact(event)).decode(), flush=True)
                return None
            return await connection.event_page(
                session, after_sequence=args.after_sequence
            )
    finally:
        await connection.disconnect()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
        if result is not None:
            print(canonical(redact(result)).decode())
        status = result.get("status") if isinstance(result, dict) else None
        if status == "failed" and result.get("error", {}).get("code") in (
            "validation",
            "conflict",
            "not_found",
        ):
            return 2
        return {"failed": 1, "unsupported": 3, "uncertain": 4}.get(status, 0)
    except ConversationError as exc:
        print(canonical({"error": redact(exc.to_dict())}).decode(), file=sys.stderr)
        return (
            4
            if exc.uncertain
            else 3
            if exc.code == "unsupported"
            else 2
            if exc.code in ("validation", "conflict", "not_found")
            else 1
        )
    except KeyboardInterrupt:
        return 130
