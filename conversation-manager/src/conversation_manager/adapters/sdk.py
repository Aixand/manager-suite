"""Official provider SDKs hosted by retained, individually owned sessions."""

import asyncio
from aixand_filesystem import lock_file
from importlib import metadata
from pathlib import Path
import uuid

from ..models import ConversationError, digest, redact
from .sdk_resources import validate, session_directory
from .sdk_session import SdkSession

from ..provider_policy import sdk_packages, compatibility

PACKAGES = {('claude-agent-sdk' if provider == 'claude' else provider + '-sdk'): value
            for provider, value in sdk_packages().items()}


class Callbacks:
    def __init__(self, adapter, identity):
        self.adapter, self.identity = adapter, identity

    def emit(self, kind, payload, native=None):
        return self.adapter.core.emit(kind, payload, self.identity, native=native)

    async def permission(self, tool, arguments, tool_id=None):
        request_id = self.identity + ":" + str(tool_id or uuid.uuid4().hex)
        response = await self.adapter.core.request(
            self.identity,
            "permission",
            "sdk/permission",
            request_id,
            choices=[
                {"optionId": "allow_once", "kind": "allow_once", "name": "Allow once"},
                {"optionId": "deny_once", "kind": "reject_once", "name": "Deny"},
            ],
            native={
                "method": "sdk/permission",
                "params": {
                    "message": "Allow " + tool + "?",
                    "tool": tool,
                    "arguments": arguments,
                },
            },
        )
        self.adapter.core.response_submitted(request_id)
        return (
            response.get("outcome") == "selected"
            and response.get("option_id") == "allow_once"
        )

    async def question(self, questions, *, manager_owned=False):
        if not questions or any(
            not isinstance(row, dict) or not isinstance(row.get("question"), str)
            for row in questions
        ):
            raise ConversationError("validation", "SDK questions require question text")
        questions = [
            {
                **row,
                "id": row.get("id") or str(index),
                "header": row.get("header", "Question"),
                "options": [
                    {
                        "label": option.get("label", option.get("text", "")),
                        "description": option.get("description", ""),
                    }
                    for option in row.get("options", [])
                ],
            }
            for index, row in enumerate(questions)
        ]
        if len({row["id"] for row in questions}) != len(questions):
            raise ConversationError("validation", "SDK question IDs must be unique")
        request_id = self.identity + ":question:" + uuid.uuid4().hex
        response = await self.adapter.core.request(
            self.identity,
            "user_input",
            "sdk/question",
            request_id,
            response_schema={
                "type": "object",
                "required": ["answers"],
                "additionalProperties": False,
                "properties": {
                    "answers": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    }
                },
            },
            native={"method": "sdk/question", "params": {"questions": questions}},
        )
        self.adapter.core.response_submitted(request_id)
        if manager_owned:
            # The manager consumes this decision itself (for example, a model
            # compaction confirmation), outside a provider-owned prompt turn.
            self.adapter.core.response_observed(request_id)
        return response["native_result"]["answers"]


class SdkAdapter:
    sdk_runtime = True
    reply_mode = "sdk_callback"

    def __init__(self, core, prepared, services=None):
        self.core, self.prepared, self.services = core, prepared, services
        self.sessions, self.locks = {}, {}
        self.protocol_version = prepared.protocol + "/1"
        self.raw, self.client_caps = {}, {}
        self.connected = False

    async def start(self):
        if self.prepared.argv or self.prepared.transport.get("kind") != "sdk":
            raise ConversationError(
                "validation", "SDK backends use an empty argv and sdk transport"
            )
        from ..sdk_runtime import activate
        activate(self.prepared.env.get("AIXAND_PROVIDER_SDK_PATH"))
        package, required = PACKAGES[self.prepared.protocol]
        if self.services and hasattr(self.services, "sdk_driver"):
            installed = "injected-fixture"
        else:
            try:
                installed = metadata.version(package)
            except metadata.PackageNotFoundError as error:
                raise ConversationError(
                    "unsupported",
                    "Install aixand-conversation-manager[" + self.prepared.adapter_id + "]",
                ) from error
            if not compatibility(self.prepared.backend_id, installed, kind='sdk', backend=self.prepared.adapter_id)['compatible']:
                raise ConversationError(
                    "unsupported",
                    f"{package} {installed} is not qualified; install {required}",
                )
        self.adapter_version = installed
        self.raw = {
            "sdk": package,
            "version": installed,
            "authentication": "provider-managed-login" if self.prepared.protocol == "copilot-sdk" else "explicit-profile-login" if any(ref.get("method") == "profile-login" for ref in self.prepared.credential_refs) else "explicit-api-credentials",
            "access": {
                "permission_modes": ["ask", "deny"],
                "os_sandbox_equivalence": False,
            },
        }
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
            "close_session",
            "instructions_delivery",
            "skills_delivery",
            "mcp_delivery",
            "access_delivery",
            "execute_command",
            "set_config",
            "session_state",
            "turn_result",
            "steer",
        }
        if self.prepared.protocol in ("claude-agent-sdk", "copilot-sdk"):
            supported.update(("set_config", "set_model"))
        if self.prepared.protocol == "copilot-sdk":
            supported.discard("steer")
        self.operations = {
            name: {
                "supported": name in supported,
                "scope": "session",
                "reason": None if name in supported else "No qualified SDK mapping",
                "evidence": {"sdk": package, "version": installed},
            }
            for name in self.core.operation_names
        }
        self.connected = True

    def validate_settings(self, settings):
        validate(settings)
        if (
            self.prepared.protocol != "claude-agent-sdk"
            and settings.get("mode", "default") != "default"
        ):
            raise ConversationError(
                "unsupported", "Planning mode has no qualified Antigravity SDK control"
            )

    def driver(self, settings, identity, directory, restore):
        callbacks = Callbacks(self, identity)
        if self.services and hasattr(self.services, "sdk_driver"):
            return self.services.sdk_driver(
                self.prepared.protocol,
                settings,
                identity,
                directory,
                restore,
                callbacks,
                self.prepared.env,
            )
        if self.prepared.protocol == "copilot-sdk":
            from .copilot_sdk import CopilotDriver
            authentication = next((ref for ref in self.prepared.credential_refs if ref.get("provider") == "copilot"), None)
            return CopilotDriver(settings, identity, directory, restore, callbacks, self.prepared.env, authentication=authentication)
        if self.prepared.protocol == "claude-agent-sdk":
            from .claude_sdk import ClaudeDriver

            cls = ClaudeDriver
        else:
            from .antigravity_sdk import AntigravityDriver

            cls = AntigravityDriver
        if self.prepared.protocol == "claude-agent-sdk":
            authentication = next((ref for ref in self.prepared.credential_refs if ref.get("provider") == "claude"), None)
            return cls(settings, identity, directory, restore, callbacks, self.prepared.env, authentication=authentication)
        return cls(settings, identity, directory, restore, callbacks, self.prepared.env)

    async def open(self, settings, identity, *, restore=False):
        self.validate_settings(settings)
        directory = session_directory(self.core.store, self.prepared, identity)
        checkpoint = self.core.store.get("sdk-session", str(directory))
        if restore:
            if (
                not checkpoint
                or checkpoint["identity"] != identity
                or checkpoint["adapter"] != self.prepared.adapter_id
            ):
                raise ConversationError(
                    "not_found", "The exact saved SDK session is unavailable"
                )
            old_refs, new_refs = (
                checkpoint["credential_refs"],
                self.prepared.credential_refs,
            )
            compatible = old_refs == new_refs or (
                len(old_refs) == len(new_refs)
                and all(
                    old.get("id")
                    and all(
                        old.get(key) == new.get(key)
                        for key in ("id", "provider", "method")
                    )
                    for old, new in zip(old_refs, new_refs)
                )
            )
            if checkpoint["settings"] != settings or not compatible:
                raise ConversationError(
                    "conflict",
                    "Restore requires the original settings and credential identity",
                )
            if checkpoint["sdk_version"] != self.adapter_version:
                raise ConversationError(
                    "unsupported",
                    "SDK version changed; saved state requires explicit migration",
                )
            if not checkpoint.get("confirmed"):
                raise ConversationError(
                    "not_found", "The SDK session has no observed provider identity yet"
                )
        lock = (directory / "owner.lock").open("a+")
        try:
            lock_file(lock)
        except BlockingIOError as error:
            lock.close()
            raise ConversationError(
                "busy", "The saved SDK session already has an owner"
            ) from error
        driver = self.driver(settings, identity, directory, restore)
        if restore and self.prepared.protocol == "copilot-sdk":
            driver.expected_account = checkpoint.get("account")
        actor = SdkSession(driver)
        initialized = False
        try:
            observed = await asyncio.shield(actor.ready)
            if observed != identity:
                raise ConversationError(
                    "uncertain",
                    "SDK returned a different session identity",
                    uncertain=True,
                )
            initialized = True
            if self.prepared.protocol == "copilot-sdk" and getattr(driver, "model", None):
                settings["model"] = driver.model
            self.sessions[identity], self.locks[identity] = actor, lock
            self.core.store.put(
                "sdk-session",
                str(directory),
                {
                    "identity": identity,
                    "adapter": self.prepared.adapter_id,
                    "sdk_version": self.adapter_version,
                    "settings": getattr(driver, "settings", settings),
                    "credential_refs": self.prepared.credential_refs,
                    "directory": str(directory),
                    "confirmed": getattr(actor.driver, "identity_observed", True),
                    **({"account": driver.account} if self.prepared.protocol == "copilot-sdk" and hasattr(driver, "account") else {}),
                },
            )
        except BaseException as error:
            self.sessions.pop(identity, None)
            self.locks.pop(identity, None)
            await actor.close()
            lock.close()
            if initialized or getattr(driver, "identity_observed", False):
                raise ConversationError(
                    "uncertain",
                    "SDK session initialized but its checkpoint could not be saved" if initialized else
                    "SDK session was created but initialization failed: " + (str(error) if isinstance(error, ConversationError) else "inspect the saved session"),
                    uncertain=True,
                    recovery={
                        "phase": "checkpoint" if initialized else "initialization",
                        "native_session_id": identity,
                        "identity_evidence": "observed"
                        if getattr(actor.driver, "identity_observed", True)
                        else "configured",
                    },
                ) from error
            raise
        return identity, {
            "session_id": identity,
            "sdk": self.raw["sdk"],
            "sdk_version": self.adapter_version,
            "identity_evidence": "observed"
            if getattr(actor.driver, "identity_observed", True)
            else "configured",
        }

    async def create(self, settings, *, operation_id):
        identity = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL, self.core.ref["connection_id"] + ":" + operation_id
            )
        )
        return await self.open(settings, identity)

    async def restore(self, saved, settings):
        return await self.open(settings, saved["native_session_id"], restore=True)

    async def load(self, session, settings):
        if (
            session["native_session_id"] not in self.sessions
            or self.sessions[session["native_session_id"]].closed
        ):
            raise ConversationError(
                "transport", "Session actor is unavailable; use explicit restore"
            )
        return {"session_id": session["native_session_id"]}

    def validate_send(self, session):
        actor = self.sessions.get(session["native_session_id"])
        if actor is None or actor.closed:
            raise ConversationError(
                "conflict",
                "SDK session is closed; restore requires an explicitly stopped worker",
            )

    def validate_content(self, session, content):
        text = "\n".join(row["text"] for row in content).lstrip()
        if text.startswith("/"):
            name = text[1:].split(None, 1)[0] if text[1:].strip() else ""
            row = next(
                (
                    row
                    for row in self.commands(session)["commands"]
                    if row["name"] == name
                ),
                None,
            )
            if row and row["execution"] in ("unsupported", "setting"):
                raise ConversationError(
                    "unsupported",
                    row.get("reason") or "Use the acknowledged SDK setting control",
                )

    async def prompt(self, session, content, turn):
        identity = session["native_session_id"]
        actor = self.sessions[identity]
        saved = self.core.store.get("session", session["session_id"])
        self.validate_settings(saved["settings"])
        self.core.emit('message.delta', {'role': 'user', 'text': '\n'.join(row['text'] for row in content)}, identity)
        try:
            return await actor.submit(
                "prompt", "\n".join(row["text"] for row in content)
            )
        finally:
            self.confirm_identity(actor, identity)

    def confirm_identity(self, actor, identity):
        if getattr(actor.driver, "identity_observed", True):
            directory = session_directory(self.core.store, self.prepared, identity)
            checkpoint = self.core.store.get("sdk-session", str(directory))
            if checkpoint and not checkpoint["confirmed"]:
                checkpoint["confirmed"] = True
                self.core.store.put("sdk-session", str(directory), checkpoint)
                self.core.emit(
                    "session.identity",
                    {"identity": identity, "evidence": "observed"},
                    identity,
                )

    def validate_command(self, name, arguments):
        # Core validates the exact session catalog separately before dispatch.
        from .sdk_commands import NAME

        if (
            not isinstance(name, str)
            or not NAME.fullmatch(name)
            or not isinstance(arguments, str)
            or "\0" in arguments
            or len(arguments) > 24000
        ):
            raise ConversationError("validation", "Invalid SDK command or arguments")

    def is_turn_command(self, name, arguments):
        from .sdk_commands import INSPECTIONS

        return name not in INSPECTIONS

    def command_requires_idle(self, name, arguments):
        return self.is_turn_command(name, arguments)

    def validate_session_command(self, session, name, arguments):
        from .sdk_commands import validate

        self.validate_send(session)
        return validate(self.commands(session), name, arguments)

    async def command_prompt(self, session, command, turn):
        self.validate_session_command(
            session, command["name"], command.get("arguments", "")
        )
        text = (
            "/"
            + command["name"]
            + (" " + command["arguments"] if command.get("arguments") else "")
        )
        actor = self.sessions[session["native_session_id"]]
        saved = self.core.store.get("session", session["session_id"])
        self.validate_settings(saved["settings"])
        self.core.emit('message.delta', {'role': 'user', 'text': text}, session['native_session_id'])
        try:
            return await actor.submit(
                "prompt",
                text,
                {"name": command["name"], "key": command["operation_id"]},
            )
        finally:
            self.confirm_identity(actor, session["native_session_id"])

    async def steer(self, session, text, turn, *, input_id):
        actor = self.sessions[session["native_session_id"]]
        await actor.submit("steer", text, input_id)

    async def cancel(self, session, turn):
        await self.sessions[session["native_session_id"]].submit("cancel")

    def validate_response(self, record, value):
        if record["kind"] == "user_input":
            if (
                set(value) != {"answers"}
                or not isinstance(value["answers"], dict)
                or any(
                    not isinstance(k, str) or not isinstance(v, str)
                    for k, v in value["answers"].items()
                )
            ):
                raise ConversationError(
                    "validation", "Answers must map question IDs to strings"
                )
            questions = record["native"]["params"]["questions"]
            if set(value["answers"]) != {row["id"] for row in questions} or any(
                not answer.strip() for answer in value["answers"].values()
            ):
                raise ConversationError(
                    "validation", "Answer every question using its exact ID"
                )

    async def configuration(self, session):
        actor = self.sessions[session["native_session_id"]]
        result = await actor.submit("configuration")
        if self.prepared.protocol == "copilot-sdk":
            model = next((row.get("currentValue") for row in result.get("options", []) if row.get("category") == "model"), None)
            if model:
                saved = self.core.store.get("session", session["session_id"])
                directory = session_directory(self.core.store, self.prepared, session["native_session_id"])
                checkpoint = self.core.store.get("sdk-session", str(directory))
                if saved and checkpoint:
                    saved["settings"] = {**saved["settings"], "model": model}
                    checkpoint["settings"] = saved["settings"]
                    actor.driver.settings["model"] = model
                    self.core.store.put_many([("session", session["session_id"], saved), ("sdk-session", str(directory), checkpoint)])
        return result

    def commands(self, session):
        return self.sessions[session["native_session_id"]].driver.commands()

    def validate_config(self, session, config_id, value):
        if config_id == "permission_mode" and value in ("ask", "deny"):
            return
        if self.prepared.protocol in ("claude-agent-sdk", "copilot-sdk") and (
            config_id == "model"
            and isinstance(value, str)
            and value.strip()
            or self.prepared.protocol == "claude-agent-sdk" and config_id == "mode"
            and value in ("default", "plan")
        ):
            return
        raise ConversationError(
            "unsupported", "This SDK setting has no qualified mutable mapping"
        )

    async def optional(self, method, session, params):
        actor = self.sessions[session["native_session_id"]]
        if method == "execute_command":
            self.validate_session_command(
                session, params["name"], params.get("arguments", "")
            )
            return {
                "output": redact(
                    await actor.submit("inspect_command", params["name"]),
                    self.core.secrets,
                )
            }
        if method in ("set_config", "set_model"):
            value = params.get("value", params.get("model"))
            config_id = params.get("config_id", "model")
            self.validate_config(session, config_id, value)
            result = await actor.submit(
                {
                    "model": "set_model",
                    "mode": "set_mode",
                    "permission_mode": "set_permissions",
                }[config_id],
                value,
            )
            saved = self.core.store.get("session", session["session_id"])
            saved["settings"] = {
                **saved["settings"],
                **(
                    {"access": {"permission_mode": value}}
                    if config_id == "permission_mode"
                    else {config_id: value}
                ),
            }
            directory = session_directory(
                self.core.store, self.prepared, session["native_session_id"]
            )
            checkpoint = self.core.store.get("sdk-session", str(directory))
            checkpoint["settings"] = saved["settings"]
            self.core.store.put_many([("session", session["session_id"], saved), ("sdk-session", str(directory), checkpoint)])
            self.core.emit("session.config", result, session["native_session_id"])
            return result
        if method == "close_session":
            await actor.close()
            lock = self.locks.pop(session["native_session_id"], None)
            if lock:
                lock.close()
            return {"closed": True}
        if method == "history":
            return self.core.store.history_page(session)
        raise ConversationError("unsupported", "No qualified SDK operation mapping")

    async def close(self):
        await asyncio.gather(
            *(actor.close() for actor in self.sessions.values()), return_exceptions=True
        )
        for lock in self.locks.values():
            lock.close()
        self.connected = False
