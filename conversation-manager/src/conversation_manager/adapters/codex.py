"""Native Codex RPC bridge; prepared resources are supplied by the caller."""

from __future__ import annotations

import asyncio

from ..models import ConversationError
from ..turn_output import TurnOutput


def captured_mcp_config(settings):
    """Translate captured stdio descriptors inside the provider adapter only."""
    import re
    from copy import deepcopy

    mcp = settings.get("mcp") or {}
    if not isinstance(mcp, dict):
        raise ConversationError(
            "validation", "Captured MCP configuration must be an object"
        )
    if "servers" not in mcp:
        if mcp and "native_config" not in mcp:
            raise ConversationError(
                "unsupported",
                "Native MCP requires captured servers or retained native_config",
            )
        return deepcopy(mcp.get("native_config", {}))
    if set(mcp) - {"servers", "native_config", "selection_digest"}:
        raise ConversationError("unsupported", "Unknown captured MCP selection fields")
    servers = mcp["servers"]
    if not isinstance(servers, list) or len(servers) > 32:
        raise ConversationError(
            "validation", "Captured MCP servers must be a bounded list"
        )
    config = {}
    for row in servers:
        if not isinstance(row, dict) or set(row) - {
            "server_name",
            "kind",
            "command",
            "args",
            "env_vars",
            "cwd",
            "startup_timeout_sec",
            "tool_timeout_sec",
        }:
            raise ConversationError(
                "unsupported", "Unsupported captured native MCP fields"
            )
        name = row.get("server_name")
        if not isinstance(name, str) or not re.fullmatch(
            r"agent_manager_[A-Za-z0-9_-]+", name
        ):
            raise ConversationError(
                "validation", "Use a captured managed MCP server name"
            )
        key = "mcp_servers." + name
        if key in config or row.get("kind", "stdio") != "stdio":
            raise ConversationError(
                "validation", "Captured stdio MCP names must be unique"
            )
        if (
            not isinstance(row.get("command"), str)
            or not row["command"]
            or "\0" in row["command"]
        ):
            raise ConversationError("validation", "Captured MCP requires a command")
        if not isinstance(row.get("args", []), list) or any(
            not isinstance(arg, str) or "\0" in arg for arg in row.get("args", [])
        ):
            raise ConversationError(
                "validation", "Captured MCP arguments must be strings"
            )
        if row.get("cwd") not in (None, settings.get("cwd")) or row.get("env_vars"):
            raise ConversationError(
                "unsupported",
                "Captured MCP must use this workspace without unbound credentials",
            )
        config[key] = {
            field: deepcopy(row[field])
            for field in (
                "command",
                "args",
                "cwd",
                "startup_timeout_sec",
                "tool_timeout_sec",
            )
            if field in row
        }
    if "native_config" in mcp and mcp["native_config"] != config:
        raise ConversationError(
            "conflict", "Native MCP configuration differs from captured servers"
        )
    return config


class CodexAdapter:
    def __init__(self, core, prepared):
        self.core, self.prepared = core, prepared
        self.pending, self.turns = {}, {}
        self.outputs = {}
        self.connected = False
        self.raw, self.client_caps = {}, {}

    async def start(self):
        endpoint = self.prepared.transport.get("endpoint")
        managed = self.prepared.transport.get("kind") == "managed-codex"
        transport = None
        if managed:
            from ..codex_runtime import CodexRuntime

            self.runtime = CodexRuntime()
            transport = await self.runtime.start(
                self.prepared.transport["agent_environment_ref"], self.prepared.cwd
            )
        if self.prepared.argv or self.prepared.transport.get("kind") not in (
            "unix-websocket",
            "managed-codex",
        ):
            raise ConversationError(
                "unsupported",
                "Native Codex uses an explicitly managed Unix WebSocket; supply a launcher for process startup",
            )
        from ..native_codex import NativeCodexConnection

        self.wire = NativeCodexConnection(
            endpoint,
            client_info=self.prepared.client_info,
            on_event=self.notification_message,
            timeout=30,
            capabilities={"experimentalApi": True},
            transport=transport,
        )
        await self.wire.connect()
        if managed:
            account = await self.wire.request("account/read", {"refreshToken": False})
            if (
                account.get("requiresOpenaiAuth", True)
                and account.get("account") is None
            ):
                raise ConversationError(
                    "authentication",
                    "Sign in to the selected Codex environment with Agent Manager first",
                )
        self.socket = self.wire._socket
        self.raw = self.wire.initialization or {}
        from ..native_controls import NativeControls

        self.controls = NativeControls(self.rpc, self.raw)
        self.connected, self.protocol_version = True, "codex-app-server"
        self.adapter_version = self.raw.get("serverInfo", {}).get(
            "version"
        ) or self.raw.get("userAgent")
        from ..provider_policy import cli_version, compatibility

        checked = compatibility(
            "codex", cli_version(self.adapter_version), backend="native-codex"
        )
        if not checked["compatible"]:
            await self.wire.close()
            self.connected = False
            raise ConversationError("unsupported", checked["reason"])
        supported = {
            "create_session",
            "load_session",
            "restore_session",
            "send",
            "events",
            "cancel",
            "permissions",
            "user_input",
            "history",
            "instructions_delivery",
            "access_delivery",
            "mcp_inventory",
            "mcp_delivery",
            "skills_delivery",
        }
        supported.add("execute_command")
        supported.update(("session_state", "turn_result", "steer"))
        if self.controls.supported:
            supported.add("set_config")
        self.operations = {
            name: {
                "supported": name in supported,
                "scope": "session",
                "reason": None
                if name in supported
                else "No verified native mapping or selection preservation",
                "evidence": {
                    "protocol": "native-codex",
                    "fixture": "native RPC payloads",
                },
            }
            for name in self.core.operation_names
        }

    async def rpc(self, method, params):
        from ..native_codex import NativeCodexError

        try:
            return await self.wire.request(method, params)
        except NativeCodexError as exc:
            raise ConversationError(
                "uncertain" if exc.uncertain else "validation",
                str(exc),
                uncertain=exc.uncertain,
            ) from exc

    def notification_message(self, message):
        if "requestId" in message:
            token = message["requestId"]
            self.core.spawn(self.answer(self.wire._requests[token], token))
        elif message["method"] == "connection/closed":
            self.connected = False
            self.core.transport_lost("Native Codex transport closed")
            for future in self.turns.values():
                if not future.done():
                    future.set_exception(ConnectionError("Native connection closed"))
        else:
            self.notification(message["method"], message.get("params", {}))

    def validate_settings(self, settings):
        if settings.get("native"):
            raise ConversationError(
                "unsupported", "Raw native settings are not verified delivery mappings"
            )
        if set(settings) - {
            "cwd",
            "model",
            "instructions",
            "access",
            "skills",
            "mcp",
            "native",
        }:
            raise ConversationError("unsupported", "Unknown native session settings")
        access = settings.get("access") or {}
        if not isinstance(access, dict) or set(access) - {"sandbox", "approvalPolicy"}:
            raise ConversationError(
                "unsupported",
                "Native access accepts explicit sandbox and approvalPolicy",
            )
        for row in settings.get("skills") or []:
            from ..skill_delivery import verify_export

            verify_export(row)
        mcp = settings.get("mcp") or {}
        if mcp:
            config = captured_mcp_config(settings)
            if not isinstance(config, dict) or any(
                not k.startswith("mcp_servers.agent_manager_") for k in config
            ):
                raise ConversationError(
                    "unsupported",
                    "Native MCP delivery requires explicit managed native_config",
                )

    async def create(self, settings):
        from ..skill_delivery import require_native_catalog

        await require_native_catalog(
            self.rpc, settings["cwd"], settings.get("skills") or []
        )
        params = {"cwd": settings["cwd"]}
        if settings.get("model"):
            params["model"] = settings["model"]
        if settings.get("instructions"):
            params["developerInstructions"] = settings["instructions"]["text"]
        params.update(settings.get("access") or {})
        if settings.get("mcp"):
            params["config"] = captured_mcp_config(settings)
        from ..native_codex import start_thread

        result = await start_thread(self.rpc, params.pop("cwd"), **params)
        self.controls.capture(result["thread"]["id"], result)
        return result["thread"]["id"], result

    async def load(self, session, settings):
        from ..skill_delivery import require_native_catalog

        await require_native_catalog(
            self.rpc, session["cwd"], settings.get("skills") or []
        )
        params = {"threadId": session["native_session_id"]}
        params.update(settings.get("access") or {})
        if settings.get("instructions"):
            params["developerInstructions"] = settings["instructions"]["text"]
        # Captured settings govern this retained session. Agent Manager's existing
        # facade applies the current-default policy to separately unloaded threads.
        if settings.get("mcp"):
            params["config"] = captured_mcp_config(settings)
        retained = (self.core.store.get("session", session["session_id"]) or {}).get(
            "native_settings"
        ) or {}
        for key in ("model", "serviceTier", "approvalPolicy", "collaborationMode"):
            if key in retained:
                params[key] = retained[key]
        if retained.get("effort"):
            params.setdefault("config", {})["model_reasoning_effort"] = retained[
                "effort"
            ]
        if retained.get("permissions"):
            params.pop("sandbox", None)
            params["permissions"] = retained["permissions"]
        from ..native_codex import resume_thread

        result = await resume_thread(self.rpc, params.pop("threadId"), **params)
        self.controls.capture(session["native_session_id"], result)
        if result["thread"]["id"] != session["native_session_id"]:
            raise ConversationError(
                "uncertain",
                "Native resume returned a different identity",
                uncertain=True,
            )
        return result

    async def restore(self, saved, settings):
        result = await self.load(saved, settings)
        return saved["native_session_id"], result

    async def prompt(self, session, content, turn):
        # Register by session before dispatch so an early completion cannot be lost.
        future = asyncio.get_running_loop().create_future()
        self.turns[session["native_session_id"]] = future
        self.outputs[session["native_session_id"]] = {}
        try:
            skills = (
                self.core.store.get("session", session["session_id"])["settings"].get(
                    "skills"
                )
                or []
            )
            from ..skill_delivery import input_items, verify_export

            for row in skills:
                verify_export(row)
            from ..native_codex import start_turn

            result = await start_turn(
                self.rpc, session["native_session_id"], content + input_items(skills)
            )
            turn["native_turn_id"] = result["turn"]["id"]
            return await self.completion(future, turn, session["native_session_id"])
        finally:
            self.turns.pop(session["native_session_id"], None)
            self.outputs.pop(session["native_session_id"], None)

    def validate_command(self, name, arguments):
        self.controls.validate_command(name, arguments)

    def is_turn_command(self, name, arguments=""):
        from ..native_command_catalog import TURN_COMMANDS

        return name in TURN_COMMANDS or name == "skills" and bool(arguments)

    @staticmethod
    def is_setting_command(name):
        from ..slash_commands import SETTING_COMMANDS

        return name in SETTING_COMMANDS

    def command_requires_idle(self, name, arguments):
        from ..slash_commands import SETTING_COMMANDS

        return name in SETTING_COMMANDS or self.is_turn_command(name, arguments)

    async def command_prompt(self, session, command, turn):
        future = asyncio.get_running_loop().create_future()
        self.turns[session["native_session_id"]] = future
        self.outputs[session["native_session_id"]] = {}
        try:
            from ..skill_delivery import input_items, verify_export

            skills = (
                self.core.store.get("session", session["session_id"])["settings"].get(
                    "skills"
                )
                or []
            )
            for skill in skills:
                verify_export(skill)
            result = await self.controls.command(
                session["native_session_id"],
                command["name"],
                command.get("arguments", ""),
                extra_inputs=input_items(skills),
            )
            if result.get("turn"):
                turn["native_turn_id"] = result["turn"]["id"]
            return await self.completion(future, turn, session["native_session_id"])
        finally:
            self.turns.pop(session["native_session_id"], None)
            self.outputs.pop(session["native_session_id"], None)

    async def completion(self, future, turn, session_id):
        completed = await future
        if completed.get("id") != turn["native_turn_id"]:
            raise ConversationError(
                "uncertain",
                "Completion belongs to a different native turn",
                uncertain=True,
            )
        status = completed.get("status")
        output = self.outputs.get(session_id, {}).pop(
            turn["native_turn_id"], TurnOutput()
        )
        # Completed items, when supplied, are authoritative over partial deltas.
        messages = [
            item
            for item in completed.get("items", [])
            if item.get("type") == "agentMessage"
        ]
        if messages:
            output.replace(messages[-1].get("text", ""), messages[-1].get("id"))
        return {
            "stop_reason": "cancelled"
            if status == "interrupted"
            else "end_turn"
            if status == "completed"
            else "error",
            "native": completed,
            "error": completed.get("error"),
            **output.result(),
        }

    async def steer(self, session, text, turn, *, input_id):
        await self.native_target(session, turn)
        try:
            await self.rpc(
                "turn/steer",
                {
                    "threadId": session["native_session_id"],
                    "expectedTurnId": turn["native_turn_id"],
                    "input": [{"type": "text", "text": text}],
                },
            )
        except ConversationError as error:
            if error.code != "validation" or error.uncertain:
                raise
            raise ConversationError("conflict", error.message) from error

    async def native_target(self, session, turn):
        try:
            async with asyncio.timeout(30):
                while not turn.get("native_turn_id"):
                    if self.core.active.get(session["session_id"]) is not turn:
                        raise ConversationError(
                            "conflict",
                            "The native turn ended before control was dispatched",
                        )
                    await asyncio.sleep(0.01)
        except TimeoutError:
            raise ConversationError(
                "busy", "The native turn is still starting; inspect its outcome"
            ) from None

    async def cancel(self, session, turn):
        await self.native_target(session, turn)
        from ..native_codex import interrupt_turn

        await interrupt_turn(
            self.rpc, session["native_session_id"], turn["native_turn_id"]
        )

    def notification(self, method, params):
        session_id = params.get("threadId")
        turn_id = params.get("turnId")
        # Load/replay and child-session notifications must not become the
        # report for a dispatched root turn.
        if session_id in self.turns and turn_id:
            output = self.outputs[session_id].setdefault(turn_id, TurnOutput())
            if method == "item/agentMessage/delta":
                output.append(params.get("delta", ""), params.get("itemId"))
            elif (
                method == "item/completed"
                and params.get("item", {}).get("type") == "agentMessage"
            ):
                output.replace(params["item"].get("text", ""), params["item"].get("id"))
            elif method == "item/started" and params.get("item", {}).get("type") in (
                "commandExecution",
                "fileChange",
                "mcpToolCall",
                "dynamicToolCall",
            ):
                output.tool()
        if method == "thread/settings/updated":
            self.controls.capture(session_id, params["threadSettings"])
            self.retain_settings(session_id)
            self.core.spawn(self.publish_configuration(session_id))
            return
        if method == "thread/tokenUsage/updated":
            self.core.emit("session.usage", params["tokenUsage"], session_id)
            return
        if method == "turn/started" and session_id in self.turns:
            session = next(
                (
                    row
                    for row in self.core.sessions.values()
                    if row["native_session_id"] == session_id
                ),
                None,
            )
            turn = self.core.active.get(session["session_id"]) if session else None
            if turn:
                turn["native_turn_id"] = params["turn"]["id"]
                self.core.emit(
                    "session.status",
                    {"turn_id": turn["turn_id"], "status": "running"},
                    session_id,
                    native=params,
                )
        if (
            method in ("turn/started", "turn/completed")
            and session_id not in self.turns
        ):
            self.core.external_turn(
                session_id, params["turn"], completed=method == "turn/completed"
            )
        if method == "turn/completed":
            future = self.turns.get(session_id)
            if future and not future.done():
                future.set_result(params["turn"])
        elif method == "item/agentMessage/delta":
            self.core.emit(
                "message.delta",
                {
                    "role": "assistant",
                    "item_id": params.get("itemId"),
                    "text": params.get("delta", ""),
                },
                session_id,
                native=params,
            )
        elif method in ("item/started", "item/completed") and params.get(
            "item", {}
        ).get("type") in ("commandExecution", "fileChange", "mcpToolCall"):
            item = params["item"]
            self.core.emit(
                "tool.start" if method == "item/started" else "tool.result",
                {
                    "tool_call_id": item["id"],
                    "status": item.get("status", "unknown"),
                    "content": item,
                },
                session_id,
                native=params,
            )
        else:
            self.core.emit(
                "native.notification",
                {"method": method, "params": params},
                session_id,
                native=params,
            )

    async def publish_configuration(self, session_id):
        self.core.emit(
            "session.config", await self.controls.configuration(session_id), session_id
        )
        self.core.emit(
            "session.commands", await self.controls.commands(session_id), session_id
        )

    def retain_settings(self, native_session):
        for session in self.core.sessions.values():
            if session["native_session_id"] == native_session:
                row = self.core.store.get("session", session["session_id"])
                if row:
                    self.core.store.put(
                        "session",
                        session["session_id"],
                        {
                            **row,
                            "native_settings": dict(
                                self.controls.settings.get(native_session) or {}
                            ),
                        },
                    )

    async def configuration(self, session):
        return await self.controls.configuration(session["native_session_id"])

    async def commands(self, session):
        return await self.controls.commands(session["native_session_id"])

    def validate_config(self, session, config_id, value):
        if config_id not in (
            "model",
            "effort",
            "serviceTier",
            "mode",
            "permissions",
        ) or not isinstance(value, str):
            raise ConversationError(
                "validation", "Choose an advertised session setting"
            )

    async def optional(self, method, session, params):
        if method == "execute_command":
            value = await self.controls.command(
                session["native_session_id"],
                params["name"],
                params.get("arguments", ""),
            )
            from ..slash_commands import SETTING_COMMANDS

            if params["name"] in SETTING_COMMANDS and "options" in value:
                self.retain_settings(session["native_session_id"])
                self.core.emit("session.config", value, session["native_session_id"])
            return value
        if method == "set_config":
            value = await self.controls.set_config(
                session["native_session_id"], params["config_id"], params["value"]
            )
            self.retain_settings(session["native_session_id"])
            self.core.emit("session.config", value, session["native_session_id"])
            return value
        if method == "history":
            from ..native_codex import read_thread

            return await read_thread(self.rpc, session["native_session_id"])
        raise ConversationError(
            "unsupported", "Operation has no verified native mapping"
        )

    async def answer(self, message, token):
        from ..native_requests import response_schema

        method, params = message["method"], message.get("params", {})
        kind = (
            "permission"
            if method.endswith("requestApproval")
            or method in ("execCommandApproval", "applyPatchApproval")
            else "user_input"
            if method.endswith("requestUserInput")
            else "native"
        )
        # Native Codex decisions are explicit native_result, never ACP option IDs.
        response = await self.core.request(
            params["threadId"],
            kind,
            method,
            message["id"],
            response_schema=response_schema(method),
            native=message,
        )
        result = response["native_result"]
        await self.wire.respond(token, result)
        self.core.response_observed(message["id"])

    def validate_response(self, record, value):
        from ..native_requests import validate_response

        validate_response(record, value)

    async def close(self):
        try:
            if hasattr(self, "wire"):
                await self.wire.close()
        finally:
            if hasattr(self, "runtime"):
                await self.runtime.close()
