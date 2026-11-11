"""OpenCode V2: a private retained server and durable native input identities.

The application owns the editable after-turn queue. Only an input dispatched by
the manager enters OpenCode's inbox; native steering never cancels a turn.
"""

import asyncio
from copy import deepcopy
import base64
import json
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
import secrets
import urllib.error
import urllib.parse
import urllib.request
import uuid

from ..models import ConversationError


class OpenCodeAdapter:
    keyed_sessions = True
    reply_mode = "sdk_callback"
    command_timeout = 120

    def __init__(self, core, prepared):
        self.core, self.prepared = core, prepared
        self.connected = False
        self.process = None
        self.server_mcp = None
        self.owner_lock = None
        self.environment_guard = None
        self.environment_lease = None
        self.sessions = {}
        self.answers = {}
        self.uncertain_commands = set()
        self.raw, self.client_caps = {}, {}
        from ..opencode_controls import OpenCodeControls

        self.controls = OpenCodeControls(self)

    async def start(self):
        descriptor = self.prepared.transport.get("agent_environment_ref")
        if descriptor is not None:
            from ..agents import AgentEnvironments

            agents = AgentEnvironments(
                "opencode", config=descriptor["config"], root=descriptor["root"]
            )
            guard = agents.lease_native(descriptor)
            try:
                lease = guard.__enter__()
            except Exception as error:
                raise ConversationError(
                    "conflict",
                    "The OpenCode environment is busy, unavailable, or changed",
                ) from error
            self.environment_guard = guard
            self.prepared.env = dict(lease.env)
            self.environment_lease = lease
            if (
                lease.descriptor["target"]["registry"]
                != descriptor["target"]["registry"]
                or lease.descriptor["target"]["home"] != descriptor["target"]["home"]
                or self.prepared.argv != [lease.descriptor["executable"]]
            ):
                raise ConversationError(
                    "conflict", "The original OpenCode environment identity changed"
                )
        if (
            not self.prepared.argv
            or self.prepared.transport.get("kind") != "opencode-http"
        ):
            raise ConversationError(
                "validation",
                "OpenCode requires a managed executable and private HTTP transport",
            )
        process = await asyncio.create_subprocess_exec(
            *self.prepared.argv,
            "--version",
            env=self.prepared.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), 15)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        from ..opencode_runtime import version_text, version_error

        self.adapter_version = (
            version_text(stdout.decode()) if not process.returncode else None
        )
        error = version_error(self.adapter_version)
        if error:
            raise ConversationError("unsupported", error)
        self.protocol_version = "opencode-v2/1"
        self.raw = {"provider": "opencode", "version": self.adapter_version}
        supported = {
            "create_session",
            "load_session",
            "restore_session",
            "send",
            "steer",
            "events",
            "cancel",
            "permissions",
            "user_input",
            "history",
            "session_state",
            "turn_result",
            "mcp_delivery",
            "instructions_delivery",
            "access_delivery",
            "set_config",
            "close_session",
        }
        if self.adapter_version == "2.2.5":
            supported.add("execute_command")
        self.operations = {
            name: dict(
                supported=name in supported,
                scope="session",
                reason=None
                if name in supported
                else "No qualified OpenCode V2 mapping",
                evidence={
                    "protocol": self.protocol_version,
                    "version": self.adapter_version,
                },
            )
            for name in self.core.operation_names
        }
        self.connected = True

    def validate_settings(self, settings):
        from .sdk_resources import validate

        normalized = deepcopy(settings)
        access = normalized.get("access") or {}
        if access and (
            set(access) != {"permission_mode"}
            or access["permission_mode"] not in ("ask", "deny", "allow")
        ):
            raise ConversationError(
                "validation",
                "OpenCode access requires permission_mode ask, deny or allow",
            )
        if access:
            normalized["access"] = {"permission_mode": "ask"}
        validate(normalized)
        if settings.get("skills"):
            raise ConversationError(
                "unsupported", "OpenCode native skill delivery is not qualified"
            )
        if settings.get("model"):
            self.model(settings["model"])

    @staticmethod
    def model(value):
        provider, separator, name = value.partition("/")
        if not separator or not provider or not name or any(c.isspace() for c in value):
            raise ConversationError(
                "validation", "OpenCode models require provider/model"
            )
        name, marker, variant = name.partition("#")
        if not name or marker and (not variant or "#" in variant):
            raise ConversationError(
                "validation", "Choose a valid provider/model#variant reference"
            )
        return {
            "providerID": provider,
            "id": name,
            **({"variant": variant} if variant else {}),
        }

    async def ensure_server(self, settings):
        selected = settings.get("mcp", {}).get("servers", [])
        if self.process:
            if self.process.returncode is not None:
                raise ConversationError(
                    "transport", "The retained OpenCode server exited", uncertain=True
                )
            if selected != self.server_mcp:
                raise ConversationError(
                    "conflict",
                    "OpenCode sessions must retain their captured MCP servers",
                )
            return
        config = {}
        for row in selected:
            if any(key not in self.prepared.env for key in row.get("env_vars", [])):
                raise ConversationError(
                    "authentication",
                    "Supply the captured MCP environment bindings before starting OpenCode",
                )
            config[row["server_name"]] = {
                "type": "local",
                "command": [row["command"], *row.get("args", [])],
                "codemode": False,
                "environment": {
                    key: self.prepared.env[key] for key in row.get("env_vars", [])
                },
                **({"cwd": row["cwd"]} if row.get("cwd") else {}),
            }
        variables = dict(self.prepared.env)
        from aixand_filesystem import lock_file

        database = variables.get("OPENCODE_DB")
        if not database:
            raise ConversationError(
                "validation", "Native OpenCode requires an isolated database"
            )
        path = Path(database)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.owner_lock = path.with_suffix(".owner.lock").open("a+")
        try:
            lock_file(self.owner_lock)
        except BlockingIOError as error:
            self.owner_lock.close()
            self.owner_lock = None
            raise ConversationError(
                "busy", "The saved OpenCode database already has an owner"
            ) from error
        # The server is private; an Agent Manager database can contain retained
        # native history. Only explicitly created/loaded sessions are exposed here.
        # Resource finalization precedes process startup and native MCP discovery.
        inline = json.loads(variables.get("OPENCODE_CONFIG_CONTENT", "{}"))
        if config:
            servers = inline.setdefault("mcp", {}).setdefault("servers", {})
            if set(servers) & set(config):
                raise ConversationError(
                    "conflict",
                    "Managed OpenCode MCP names conflict with inline configuration",
                )
            servers.update(config)
        password = secrets.token_urlsafe(32)
        variables.update(
            OPENCODE_SERVER_USERNAME="opencode",
            OPENCODE_SERVER_PASSWORD=password,
            OPENCODE_CONFIG_CONTENT=json.dumps(inline),
        )
        self.authorization = (
            "Basic " + base64.b64encode(("opencode:" + password).encode()).decode()
        )
        from aixand_local_runtime import process_start

        identity = process_start(os.getpid())
        if identity is None:
            raise ConversationError(
                "unsupported", "Native process ownership could not be verified"
            )
        command = [sys.executable, "-I", "-B", "-m", "conversation_manager.native_process",
                   "--owner", str(os.getpid()), "--owner-start", identity, "--",
                   *self.prepared.argv, "serve", "--stdio", "--port", "0",
                   "--hostname", "127.0.0.1"]
        options = (self.environment_lease.subprocess_options()
                   if self.environment_lease is not None else nullcontext({}))
        with options as inherited:
            self.process = await asyncio.create_subprocess_exec(
                *command, cwd=self.prepared.cwd, env=variables,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=65536,
                **inherited,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
            )
        try:
            line = await asyncio.wait_for(self.process.stdout.readline(), 15)
            self.url = json.loads(line)["url"]
            parsed = urllib.parse.urlparse(self.url)
            if (
                parsed.scheme != "http"
                or parsed.hostname != "127.0.0.1"
                or not parsed.port
            ):
                raise ValueError("Invalid private server endpoint")
            schema = await self.http("GET", "/openapi.json")
            delivery = schema["components"]["schemas"]["Session.Inbox.Delivery"]["enum"]
            if set(delivery) != {"steer", "queue"}:
                raise ValueError("Unexpected native input contract")
            # V2's headless API does not register MCP declarations from config.
            # Register captured servers in this private runtime before inference.
            location = "?" + urllib.parse.urlencode(
                {"location[directory]": settings["cwd"]}
            )
            for name, definition in config.items():
                await self.http(
                    "PUT",
                    "/api/experimental/mcp/"
                    + urllib.parse.quote(name, safe="")
                    + location,
                    {"config": definition},
                )
            required = {
                row["key"]
                for row in (self.prepared.resource_policy or {}).get("items", [])
                if row["kind"] == "mcp" and row["required"]
            }
            if self.prepared.resource_policy is None:
                required = set(config)
            if required:
                try:
                    async with asyncio.timeout(20):
                        while True:
                            rows = (await self.http("GET", "/api/mcp" + location))[
                                "data"
                            ]
                            states = {
                                row["name"]: row["status"]["status"] for row in rows
                            }
                            if any(states.get(name) == "failed" for name in required):
                                raise ConversationError(
                                    "resource_unavailable",
                                    "A required OpenCode MCP server failed to connect",
                                )
                            if all(
                                states.get(name) == "connected" for name in required
                            ):
                                break
                            await asyncio.sleep(0.05)
                except TimeoutError:
                    raise ConversationError(
                        "resource_unavailable",
                        "Required OpenCode MCP tools did not become ready",
                    ) from None
            self.server_mcp = selected
        except BaseException:
            await self.close()
            raise
        self.monitor = asyncio.create_task(self.watch())
        self.stdout_task = asyncio.create_task(self.drain_stdout())

    async def drain_stdout(self):
        while await self.process.stdout.read(65536):
            pass

    async def watch(self):
        await self.process.wait()
        if self.connected:
            self.connected = False
            self.core.transport_lost("OpenCode private server exited")

    async def http(self, method, path, body=None):
        def request():
            wire = urllib.request.Request(
                self.url + path,
                method=method,
                data=json.dumps(body).encode() if body is not None else None,
                headers={
                    "Authorization": self.authorization,
                    "Content-Type": "application/json",
                },
            )
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(wire, timeout=15) as response:
                    data = response.read()
                    return json.loads(data) if data else {}
            except urllib.error.HTTPError as error:
                code = {
                    400: "validation",
                    401: "authentication",
                    404: "not_found",
                    409: "conflict",
                }.get(error.code, "transport")
                raise ConversationError(
                    code,
                    "OpenCode request failed (HTTP " + str(error.code) + ")",
                    uncertain=error.code >= 500,
                ) from None
            except (OSError, ValueError) as error:
                raise ConversationError(
                    "transport",
                    "OpenCode response could not be confirmed",
                    uncertain=method != "GET",
                ) from error

        return await asyncio.to_thread(request)

    async def create(self, settings, *, operation_id):
        self.validate_settings(settings)
        await self.ensure_server(settings)
        identity = (
            "ses_"
            + uuid.uuid5(
                uuid.NAMESPACE_URL, self.core.ref["connection_id"] + ":" + operation_id
            ).hex
        )
        body = {"id": identity, "location": {"directory": settings["cwd"]}}
        if settings.get("model"):
            body["model"] = self.model(settings["model"])
        permission = settings.get("access", {}).get("permission_mode")
        if permission:
            body["permissions"] = self.controls.permission_rules(permission)
        if settings.get("mode") == "plan":
            from ..opencode_controls import PLAN_AGENT, PLAN_RULES

            body["agent"] = PLAN_AGENT
            body["permissions"] = body.get("permissions", []) + deepcopy(PLAN_RULES)
        observed = (await self.http("POST", "/api/session", body))["data"]
        if observed["id"] != identity:
            raise ConversationError(
                "uncertain",
                "OpenCode returned another session identity",
                uncertain=True,
            )
        if settings.get("instructions"):
            await self.http(
                "PUT",
                "/api/experimental/session/"
                + identity
                + "/instructions/entries/aixand",
                {"value": settings["instructions"]["text"]},
            )
        self.sessions[identity] = settings
        self.core.store.put(
            "opencode-controls",
            identity,
            dict(
                default_agent=(observed.get("agent") or "build")
                if settings.get("mode") != "plan"
                else "build",
                base_rules=[]
                if permission or settings.get("mode") == "plan"
                else observed.get("permissions", []),
                permission_mode=permission or "native",
            ),
        )
        return identity, {"session_id": identity, "identity_evidence": "observed"}

    async def load(self, session, settings):
        self.validate_settings(settings)
        await self.ensure_server(settings)
        identity = session["native_session_id"]
        observed = (await self.http("GET", "/api/session/" + identity))["data"]
        if (
            observed["id"] != identity
            or observed["location"]["directory"] != settings["cwd"]
        ):
            raise ConversationError(
                "conflict", "Saved OpenCode identity or workspace differs"
            )
        if identity in (await self.http("GET", "/api/session/active"))["data"]:
            raise ConversationError(
                "busy", "The saved OpenCode session is already running"
            )
        self.sessions[identity] = settings
        return {"session_id": identity, "identity_evidence": "observed"}

    async def restore(self, saved, settings):
        return saved["native_session_id"], await self.load(saved, settings)

    def validate_send(self, session):
        if session["native_session_id"] not in self.sessions:
            raise ConversationError("conflict", "Load the exact OpenCode session first")
        if session["native_session_id"] in self.uncertain_commands:
            raise ConversationError(
                "uncertain",
                "A native command outcome is unknown; inspect and explicitly recover this runtime before more work",
                uncertain=True,
            )

    async def messages(self, identity, *, stop_at=None, first_page=False):
        # Read the newest page first, retaining all messages in long tool loops.
        path = "/api/session/" + identity + "/message?limit=100&order=desc"
        result = []
        while path:
            page = await self.http("GET", path)
            result.extend(page["data"])
            if (
                first_page
                or stop_at
                and any(row["id"] in stop_at for row in page["data"])
            ):
                break
            cursor = page.get("cursor", {}).get("next")
            path = (
                "/api/session/"
                + identity
                + "/message?limit=100&cursor="
                + urllib.parse.quote(cursor, safe="")
                if cursor and len(page["data"]) == 100
                else None
            )
        return list(reversed(result))

    def project(self, identity, message, observed):
        if message.get("type") != "assistant":
            return
        for index, part in enumerate(message.get("content", [])):
            key = message["id"] + ":" + str(index)
            if part["type"] in ("text", "reasoning"):
                text = part.get("text", "")
                before = observed.get(key, "")
                if text.startswith(before) and len(text) > len(before):
                    self.core.emit(
                        "message.delta",
                        {
                            "role": "assistant"
                            if part["type"] == "text"
                            else "reasoning",
                            "item_id": key,
                            "text": text[len(before) :],
                        },
                        identity,
                    )
                observed[key] = text
            elif part["type"] == "tool":
                fingerprint = json.dumps(part, sort_keys=True)
                if observed.get(key) != fingerprint:
                    state = part.get("state", {})
                    self.core.emit(
                        "tool.update",
                        {
                            "tool_call_id": part["id"],
                            "status": state.get("type", state.get("status")),
                            "content": {"name": part["name"], **state},
                        },
                        identity,
                    )
                    observed[key] = fingerprint

    async def prompt(self, session, content, turn):
        identity = session["native_session_id"]
        baseline = {row["id"] for row in await self.messages(identity, first_page=True)}
        if turn.get("cancel_requested"):
            return {"stop_reason": "cancelled", "final_text": ""}
        input_id = "msg_" + uuid.uuid5(uuid.NAMESPACE_URL, turn["operation_id"]).hex
        admitted = (
            await self.http(
                "POST",
                "/api/session/" + identity + "/prompt",
                {
                    "id": input_id,
                    "text": "\n".join(row["text"] for row in content),
                    "delivery": "steer",
                },
            )
        )["data"]
        if admitted["id"] != input_id:
            raise ConversationError(
                "uncertain", "OpenCode input identity differs", uncertain=True
            )
        turn["native_turn_id"] = input_id
        return await self.observe_turn(session, turn, baseline)

    async def observe_turn(self, session, turn, baseline):
        identity = session["native_session_id"]
        if turn.get("cancel_requested"):
            await self.http("POST", "/api/session/" + identity + "/interrupt")
        observed = {}
        while True:
            if not self.connected:
                raise ConversationError(
                    "transport",
                    "OpenCode transport ended during the turn",
                    uncertain=True,
                )
            rows = await self.messages(identity, stop_at=baseline)
            new = [row for row in rows if row["id"] not in baseline]
            for message in new:
                self.project(identity, message, observed)
            idle = next((row for row in reversed(new) if row["type"] == "idle"), None)
            if idle:
                outcome = idle["outcome"]
                final = next(
                    (row for row in reversed(new) if row["type"] == "assistant"), {}
                )
                text = "\n".join(
                    part.get("text", "")
                    for part in final.get("content", [])
                    if part["type"] == "text"
                )
                error = None
                if outcome == "failed":
                    detail = idle.get("error") or final.get("error") or {}
                    if not isinstance(detail, dict):
                        detail = {}
                    error = {
                        "code": detail.get("type", "provider"),
                        "message": str(
                            detail.get("message")
                            or "OpenCode failed without provider error details"
                        )[:2000],
                    }
                    if isinstance(detail.get("status"), int):
                        error["status"] = detail["status"]
                return {
                    "stop_reason": "end_turn"
                    if outcome == "succeeded"
                    else "cancelled"
                    if outcome == "interrupted"
                    else "error",
                    "final_text": text,
                    "error": error,
                    "native": {"idle_id": idle["id"], "outcome": outcome},
                }
            await self.pending_requests(identity)
            await asyncio.sleep(0.2)

    @staticmethod
    def validate_command(name, arguments):
        from ..slash_commands import SETTING_COMMANDS

        if (
            name
            not in (
                "review",
                "init",
                "compact",
                "rename",
                "diff",
                "status",
                "debug-config",
                "usage",
                "mcp",
            )
            and name not in SETTING_COMMANDS
        ):
            raise ConversationError(
                "unsupported", "This command has no qualified OpenCode chat mapping"
            )
        if (
            name in ("compact", "diff", "status", "debug-config", "usage", "mcp")
            and arguments
        ):
            raise ConversationError("validation", "This command takes no arguments")
        if name == "rename" and (
            not isinstance(arguments, str) or not arguments.strip()
        ):
            raise ConversationError("validation", "Provide a conversation name")
        if (
            not isinstance(arguments, str)
            or "\0" in arguments
            or len(arguments) > 24000
        ):
            raise ConversationError(
                "validation",
                "Command arguments must be text of at most 24000 characters without NUL",
            )

    @staticmethod
    def is_turn_command(name, arguments=""):
        return name in ("review", "init", "compact")

    @staticmethod
    def is_setting_command(name):
        from ..slash_commands import SETTING_COMMANDS

        return name in SETTING_COMMANDS

    @staticmethod
    def command_requires_idle(name, arguments=""):
        return True

    async def command_prompt(self, session, command, turn):
        identity = session["native_session_id"]
        self.validate_command(command["name"], command.get("arguments", ""))
        handed_off = False
        try:
            if command["name"] == "compact":
                baseline = {
                    row["id"] for row in await self.messages(identity, first_page=True)
                }
                input_id = (
                    "msg_" + uuid.uuid5(uuid.NAMESPACE_URL, turn["operation_id"]).hex
                )
                async with asyncio.timeout(self.command_timeout):
                    await self.http(
                        "POST",
                        "/api/session/" + identity + "/compact",
                        {"id": input_id, "delivery": "steer"},
                    )
                    handed_off = True
                    turn["native_turn_id"] = input_id
                    result = await self.observe_turn(session, turn, baseline)
                    self.core.emit(
                        "session.compacted", {"command": "compact"}, identity
                    )
                    return result
            rows = (await self.commands(session))["commands"]
            if not any(
                row["name"] == command["name"] and row["execution"] == "native"
                for row in rows
            ):
                raise ConversationError(
                    "unsupported",
                    "This command is not advertised and qualified for the selected conversation",
                )
            baseline = {
                row["id"] for row in await self.messages(identity, first_page=True)
            }
            if turn.get("cancel_requested"):
                return {"stop_reason": "cancelled", "final_text": ""}
            if identity in (await self.http("GET", "/api/session/active"))["data"]:
                raise ConversationError(
                    "busy", "The native session already has active work"
                )
            async with asyncio.timeout(self.command_timeout):
                # The callback has no idempotency/input-ID field. Core journals
                # this operation before dispatch and never replays an unknown result.
                await self.http(
                    "POST",
                    "/api/session/" + identity + "/command",
                    {
                        "name": command["name"],
                        "text": command.get("arguments", ""),
                        "delivery": "steer",
                    },
                )
                handed_off = True
                result = await self.observe_turn(session, turn, baseline)
                result["native"].update(
                    command=command["name"],
                    input_identity="not-provided-by-native-callback",
                )
                return result
        except TimeoutError as error:
            self.uncertain_commands.add(identity)
            raise ConversationError(
                "uncertain",
                "Native command completion was not observed; do not resend",
                uncertain=True,
            ) from error
        except ConversationError as error:
            if handed_off or error.uncertain:
                self.uncertain_commands.add(identity)
                if not error.uncertain:
                    raise ConversationError(
                        "uncertain",
                        "Native command completion is unconfirmed; do not resend",
                        uncertain=True,
                    ) from error
            raise
        except Exception as error:
            if handed_off:
                self.uncertain_commands.add(identity)
                raise ConversationError(
                    "uncertain",
                    "Native command completion is unconfirmed; do not resend",
                    uncertain=True,
                ) from error
            raise

    async def steer(self, session, text, turn, *, input_id):
        identity = session["native_session_id"]
        if identity not in (await self.http("GET", "/api/session/active"))["data"]:
            raise ConversationError("busy", "The native turn finished before steering")
        native_id = "msg_" + uuid.uuid5(uuid.NAMESPACE_URL, input_id).hex
        await self.http(
            "POST",
            "/api/session/" + identity + "/prompt",
            {"id": native_id, "text": text, "delivery": "steer", "resume": False},
        )
        # A native completion can race admission. Never leave an unconsumed
        # input behind to be run by a later, unrelated task.
        if identity not in (await self.http("GET", "/api/session/active"))["data"]:
            inbox = (await self.http("GET", "/api/session/" + identity + "/inbox"))[
                "data"
            ]
            if any(row["id"] == native_id for row in inbox):
                try:
                    await self.http(
                        "DELETE", "/api/session/" + identity + "/inbox/" + native_id
                    )
                except ConversationError as error:
                    if error.code != "not_found":
                        raise
                else:
                    raise ConversationError(
                        "busy", "The native turn finished before consuming steering"
                    )

    async def cancel(self, session, turn):
        turn["cancel_requested"] = True
        await self.http(
            "POST", "/api/session/" + session["native_session_id"] + "/interrupt"
        )

    async def pending_requests(self, identity):
        for row in (await self.http("GET", "/api/session/" + identity + "/permission"))[
            "data"
        ]:
            if row["id"] not in self.answers:
                self.answers[row["id"]] = asyncio.create_task(
                    self.permission(identity, row)
                )
        for row in (await self.http("GET", "/api/session/" + identity + "/form"))[
            "data"
        ]:
            if row["id"] not in self.answers:
                self.answers[row["id"]] = asyncio.create_task(self.form(identity, row))
        for task in self.answers.values():
            if task.done() and not task.cancelled() and task.exception():
                raise task.exception()

    async def permission(self, identity, row):
        if self.sessions[identity].get("access", {}).get("permission_mode") == "deny":
            await self.http(
                "POST",
                "/api/session/" + identity + "/permission/" + row["id"] + "/reply",
                {"decision": "reject"},
            )
            return
        response = await self.core.request(
            identity,
            "permission",
            "opencode/permission",
            row["id"],
            choices=[
                {"optionId": "once", "kind": "allow_once", "name": "Allow once"},
                {"optionId": "reject", "kind": "reject_once", "name": "Deny"},
            ],
            native={
                "method": "conversation-manager/request",
                "params": {
                    "message": row.get("message")
                    or row["action"] + ": " + ", ".join(row["resources"])
                },
            },
        )
        await self.http(
            "POST",
            "/api/session/" + identity + "/permission/" + row["id"] + "/reply",
            {
                "decision": response.get("option_id", "reject")
                if response.get("outcome") == "selected"
                else "reject"
            },
        )
        self.core.response_observed(row["id"])

    async def form(self, identity, row):
        fields = row["fields"]
        questions = [
            {
                "id": field["key"],
                "header": field.get("title", "Question"),
                "question": field.get("description")
                or field.get("title")
                or field["key"],
                "options": [
                    {
                        "label": str(option.get("label", option.get("value", ""))),
                        "description": "",
                    }
                    for option in field.get("options", [])
                ],
            }
            for field in fields
        ]
        response = await self.core.request(
            identity,
            "user_input",
            "opencode/form",
            row["id"],
            response_schema={
                "type": "object",
                "required": ["answers"],
                "properties": {
                    "answers": {
                        "type": "object",
                        "required": [field["key"] for field in fields],
                        "properties": {
                            field["key"]: {"type": "string"} for field in fields
                        },
                        "additionalProperties": False,
                    }
                },
                "additionalProperties": False,
            },
            native={
                "method": "sdk/question",
                "params": {"questions": questions, "fields": fields},
            },
        )
        answers = response["native_result"]["answers"]
        typed = {}
        for field in fields:
            value = answers[field["key"]]
            if field["type"] in ("number", "integer", "boolean", "multiselect"):
                value = json.loads(value)
            else:
                value = next(
                    (
                        option["value"]
                        for option in field.get("options", [])
                        if option["label"] == value
                    ),
                    value,
                )
            typed[field["key"]] = value
        await self.http(
            "POST",
            "/api/session/" + identity + "/form/" + row["id"] + "/reply",
            {"answer": typed},
        )
        self.core.response_observed(row["id"])

    def validate_response(self, record, value):
        fields = record.get("native", {}).get("params", {}).get("fields", [])
        answers = value.get("answers")
        if not isinstance(answers, dict) or set(answers) != {
            field["key"] for field in fields
        }:
            raise ConversationError("validation", "Answer each OpenCode form field")
        for field in fields:
            answer = answers[field["key"]]
            if not isinstance(answer, str):
                raise ConversationError("validation", "Form answers must be strings")
            if field["type"] in ("number", "integer", "boolean", "multiselect"):
                try:
                    parsed = json.loads(answer)
                except ValueError:
                    raise ConversationError(
                        "validation",
                        "Use JSON for numeric, boolean and multiple-choice answers",
                    ) from None
                valid = (
                    type(parsed) is bool
                    if field["type"] == "boolean"
                    else isinstance(parsed, list)
                    and all(isinstance(row, str) for row in parsed)
                    if field["type"] == "multiselect"
                    else type(parsed) is int
                    if field["type"] == "integer"
                    else type(parsed) in (int, float)
                )
                if not valid:
                    raise ConversationError(
                        "validation", "The form answer has the wrong value type"
                    )

    async def configuration(self, session):
        return await self.controls.configuration(session)

    async def commands(self, session):
        settings = self.sessions.get(session["native_session_id"])
        if settings is None:
            raise ConversationError(
                "conflict", "Load the exact OpenCode session before command discovery"
            )
        location = "?" + urllib.parse.urlencode(
            {"location[directory]": settings["cwd"]}
        )
        try:
            # Agent initialization registers built-in native callbacks in V2.
            # Discovering commands before this yields a misleading empty catalog.
            await self.http("GET", "/api/agent" + location)
            value = await self.http("GET", "/api/command" + location)
        except ConversationError as error:
            if error.code == "not_found":
                return {
                    "state": "unsupported",
                    "commands": [],
                    "source": "opencode-native-api",
                }
            raise
        if not isinstance(value, dict) or not isinstance(value.get("data"), list):
            raise ConversationError(
                "validation", "OpenCode returned an invalid command catalog"
            )
        rows = []
        for row in value["data"]:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                raise ConversationError(
                    "validation", "OpenCode returned an invalid command descriptor"
                )
            qualified = (
                row["name"] in ("review", "init") and self.adapter_version == "2.2.5"
            )
            rows.append(
                dict(
                    name=row["name"],
                    description=row.get("description", row["name"]),
                    execution="native" if qualified else "unsupported",
                    source="opencode-native-api",
                    requiresIdle=True,
                    acceptsArguments=qualified and row["name"] == "review",
                    inputHint="optional native review arguments"
                    if qualified and row["name"] == "review"
                    else None,
                    reason=None
                    if qualified
                    else "Command discovered; native callback execution and completion are not yet qualified",
                )
            )
        direct = {
            "compact": "Compact conversation context",
            "rename": "Rename this conversation",
            "diff": "Inspect session file changes",
            "status": "Inspect conversation settings",
            "debug-config": "Inspect effective conversation settings",
            "usage": "Inspect session token usage",
            "mcp": "Inspect native MCP status",
        }
        for name, description in direct.items():
            rows = [row for row in rows if row["name"] != name]
            rows.append(
                dict(
                    name=name,
                    description=description,
                    execution="native",
                    requiresIdle=True,
                    inputHint="conversation name" if name == "rename" else None,
                )
            )
        configuration = await self.configuration(session)
        by_id = {row["id"]: row for row in configuration["options"]}
        for name, key in [
            ("model", "model"),
            ("reasoning", "effort"),
            ("effort", "effort"),
            ("fast", "serviceTier"),
            ("speed", "serviceTier"),
            ("permissions", "permissions"),
            ("plan", "mode"),
            ("mode", "mode"),
            ("variant", "variant"),
        ]:
            rows = [row for row in rows if row["name"] != name]
            option = by_id.get(key)
            if (
                name == "fast"
                and option
                and not any(
                    choice["value"] in ("fast", "priority")
                    for choice in option["options"]
                )
            ):
                option = None
            rows.append(
                dict(
                    name=name,
                    description="Choose " + key,
                    configId=key,
                    requiresIdle=True,
                    execution="setting" if option else "unsupported",
                    options=option["options"] if option else [],
                    inputHint=" | ".join(str(row["value"]) for row in option["options"])
                    if option
                    else None,
                    reason=None
                    if option
                    else "The selected model does not advertise this native control",
                )
            )
        return {
            "state": "ready",
            "commands": rows,
            "source": "opencode-native-api",
            "scope": "registered-native-callbacks",
            "complete": False,
        }

    def validate_config(self, session, config_id, value):
        if config_id not in (
            "model",
            "variant",
            "effort",
            "serviceTier",
            "mode",
            "permissions",
        ) or not isinstance(value, str):
            raise ConversationError(
                "validation", "Choose an advertised session setting"
            )

    async def optional(self, method, session, params):
        if method == "set_config":
            self.validate_config(session, params["config_id"], params["value"])
            return await self.controls.set_config(
                session, params["config_id"], params["value"]
            )
        if method == "execute_command":
            name, arguments = params["name"], params.get("arguments", "")
            self.validate_command(name, arguments)
            identity = session["native_session_id"]
            from ..slash_commands import SETTING_COMMANDS, setting_selection

            if name in SETTING_COMMANDS:
                key, value, option = setting_selection(
                    name, arguments, await self.configuration(session)
                )
                return (
                    {"output": option}
                    if value is None
                    else await self.controls.set_config(session, key, value)
                )
            if name == "rename":
                await self.http(
                    "PATCH", "/api/session/" + identity, {"title": arguments}
                )
                try:
                    observed = (await self.http("GET", "/api/session/" + identity))[
                        "data"
                    ]
                except Exception as error:
                    raise ConversationError(
                        "uncertain",
                        "Native rename readback is unavailable",
                        uncertain=True,
                    ) from error
                if observed.get("title") != arguments:
                    raise ConversationError(
                        "uncertain",
                        "Rename lacks matching native readback",
                        uncertain=True,
                    )
                return dict(title=arguments, output="Conversation renamed.")
            if name in ("status", "debug-config"):
                return dict(output=await self.configuration(session))
            if name == "usage":
                info = (await self.http("GET", "/api/session/" + identity))["data"]
                return dict(output={key: info.get(key) for key in ("cost", "tokens")})
            if name == "diff":
                return dict(
                    output=(
                        await self.http("GET", "/api/session/" + identity + "/diff")
                    )["data"]
                )
            if name == "mcp":
                location = "?" + urllib.parse.urlencode(
                    {"location[directory]": session["cwd"]}
                )
                return dict(output=await self.http("GET", "/api/mcp" + location))
            raise ConversationError(
                "unsupported", "Use the turn-command execution path"
            )
        if method == "history":
            return self.core.store.history_page(session)
        if method == "close_session":
            self.sessions.pop(session["native_session_id"], None)
            return {"closed": True}
        raise ConversationError(
            "unsupported", "No qualified OpenCode operation mapping"
        )

    async def close(self):
        self.connected = False
        for task in self.answers.values():
            task.cancel()
        await asyncio.gather(*self.answers.values(), return_exceptions=True)
        if self.process:
            if self.process.stdin:
                self.process.stdin.close()
            # Allow the owner to stop all provider processes after stdin closes.
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(self.process.wait(), 5)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
        if hasattr(self, "monitor"):
            await self.monitor
        if hasattr(self, "stdout_task"):
            await self.stdout_task
        if self.owner_lock:
            self.owner_lock.close()
            self.owner_lock = None
        if self.environment_guard:
            self.environment_guard.__exit__(None, None, None)
            self.environment_guard = None
