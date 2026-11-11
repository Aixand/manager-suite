"""Public frontend API. Detach and reconnect never own a backend process."""

from __future__ import annotations
import asyncio
from dataclasses import replace
from copy import deepcopy
import uuid

from .models import (
    ConnectionRef,
    OperationReceipt,
    PreparedLaunch,
    operation_key,
    receipt,
    ConversationError,
)
from .store import Store
from .transport import LocalConnector


class Client:
    def __init__(
        self,
        home=None,
        *,
        agent_config=None,
        agent_root=None,
        runtime=None,
        config=None,
        runtime_options=None,
    ):
        from .runtime import runtime_options as normalize_runtime

        from .config import load, runtime_defaults, state_home

        self.configuration = load(config)
        kind, options = runtime_defaults(self.configuration, runtime, runtime_options)
        self.runtime = normalize_runtime(kind, options)
        self.store = Store(state_home(home, settings=self.configuration))
        self.tasks = set()
        self.agent_config, self.agent_root = agent_config, agent_root

    async def available_agents(self, *, providers=("codex", "opencode")):
        """Discover Agent Manager targets and implemented startup paths, without launching."""
        from .frontend import available_agents

        return await available_agents(self, providers)

    async def start_conversation(
        self, *, agent, cwd, settings=None, operation_id, process=None
    ):
        """Prepare, launch, connect, and create under one durable startup operation."""
        from .frontend import start_conversation

        return await start_conversation(
            self,
            agent=agent,
            cwd=cwd,
            settings=settings,
            operation_id=operation_id,
            process=process,
        )

    async def attach_conversation(self, reference):
        saved = await asyncio.to_thread(self._saved_session, reference)
        reference = saved["session_ref"]["recovery"]["connection_ref"]
        runtime = reference["runtime_ref"]
        if runtime.get("owner") in ("conversation-manager-standalone", "conversation-manager-undercurrent"):
            observation = await self.inspect_runtime(runtime)
            if observation.get("state") in ("stopped", "exited", "failed"):
                raise ConversationError(
                    "runtime_stopped", "The saved conversation worker has stopped; restore the saved conversation",
                    recovery={"session_ref": saved["session_ref"], "runtime_ref": runtime},
                )
        connection = await self.connect(reference)
        try:
            return await connection.attach_conversation(saved["session_ref"])
        except BaseException:
            await connection.disconnect()
            raise

    async def resume_conversation(self, reference, *, operation_id):
        """Restore the same native session after its owner stops. Do not replay work."""
        from .recovery import resume_conversation

        return await resume_conversation(self, reference, operation_id=operation_id)

    async def stop_startup(self, startup_operation_id, *, operation_id):
        """Explicitly terminate the runtime retained by a startup, even if creation failed."""
        startup = await self.operation(startup_operation_id)
        runtime = startup.get("recovery", {}).get("runtime_ref")
        if runtime is None and startup.get("recovery", {}).get("launch_operation_id"):
            try:
                launched = await self.operation(
                    startup["recovery"]["launch_operation_id"]
                )
            except ConversationError as error:
                if error.code != "not_found":
                    raise
            else:
                runtime = launched.get("recovery", {}).get("runtime_ref")
                if runtime is None:
                    reference = (launched.get("result") or {}).get("connection_ref")
                    runtime = reference.get("runtime_ref") if reference else None
        if runtime is None:
            raise ConversationError(
                "not_found", "This startup has no recorded runtime to stop"
            )
        return await self.terminate_runtime(runtime, operation_id=operation_id)

    async def inspect_runtime(self, runtime_ref):
        from .runtime import launcher

        return await launcher(self, runtime_ref).inspect(runtime_ref)

    async def terminate_runtime(self, runtime_ref, *, operation_id):
        from .runtime import launcher

        return await launcher(self, runtime_ref).terminate(
            runtime_ref, operation_id=operation_id
        )

    @property
    def home(self):
        """Resolved local storage location; this read does not create a store."""
        return self.store.home

    def _saved_session(self, session_ref):
        if (
            not isinstance(session_ref, dict)
            or session_ref.get("schema") != "conversation-manager/session-ref-1"
        ):
            raise ConversationError(
                "validation", "Use an exact saved session reference"
            )
        saved = self.store.get("session", session_ref.get("session_id"))
        if not saved or saved["session_ref"] != session_ref:
            raise ConversationError(
                "conflict", "Saved session identity does not match the recorded session"
            )
        return saved

    async def prepare_restore(
        self,
        session_ref,
        prepared,
        *,
        env=None,
        ambient_env=None,
        credential_refs=None,
        transport=None,
    ):
        """Read saved settings for an explicit replacement, without dispatching it.

        The caller still owns native-home/credential authorization. Session
        restoration on a replacement connection separately verifies termination
        of the previous process owner.
        """
        saved = await asyncio.to_thread(self._saved_session, session_ref)
        base = (
            PreparedLaunch.from_dict(prepared)
            if isinstance(prepared, dict)
            else prepared
        )
        if (
            base.backend_id != session_ref["backend_id"]
            or base.adapter_id != session_ref["adapter_id"]
        ):
            raise ConversationError(
                "conflict", "Replacement backend differs from the saved session"
            )
        variables = dict(env or {})
        for server in saved["settings"].get("mcp", {}).get("servers", []):
            for key in server.get("env_vars", []):
                if key in (ambient_env or {}) and key not in variables:
                    variables[key] = ambient_env[key]
        return replace(
            base,
            connection_id=None,
            cwd=session_ref["cwd"],
            settings=deepcopy(saved["settings"]),
            resource_policy=None,
            resource_snapshot_ref=session_ref.get("resource_snapshot_ref"),
            env=variables,
            credential_refs=deepcopy(
                base.credential_refs if credential_refs is None else credential_refs
            ),
            transport=deepcopy(base.transport if transport is None else transport),
        )

    async def history(self, session_ref, *, cursor=0, limit=100):
        await asyncio.to_thread(self._saved_session, session_ref)
        return await asyncio.to_thread(
            self.store.history_page, session_ref, cursor, limit
        )

    async def conversations(self, *, detailed=False):
        """List locally recorded conversation references without launching agents."""
        records = await asyncio.to_thread(self.store.all, "session")
        references = [deepcopy(record["session_ref"]) for record in records]
        if not detailed:
            return references
        result = []
        for reference in references:
            try:
                conversation = await self.attach_conversation(reference)
                try:
                    state = await conversation.state()
                    result.append(
                        dict(
                            reference=reference,
                            availability="closed" if state["closed"] else "available",
                            state=state,
                        )
                    )
                finally:
                    await conversation.disconnect()
            except ConversationError as error:
                result.append(
                    dict(
                        reference=reference,
                        availability="unavailable",
                        reason=error.code,
                    )
                )
        return result

    async def recorded_queue(self, session_ref):
        """Last persisted snapshot; does not imply a reachable or running worker."""
        await asyncio.to_thread(self._saved_session, session_ref)
        value = await asyncio.to_thread(
            self.store.get, "message-queue", session_ref["session_id"]
        )
        return {
            "schema": "conversation-manager/message-queue-1",
            "source": "last-recorded",
            **(value or {"paused": False, "reason": None, "items": []}),
        }

    async def inspect_queued(self, session_ref, queue_id):
        await asyncio.to_thread(self._saved_session, session_ref)
        from .message_queue import inspect_delivery

        return await asyncio.to_thread(
            inspect_delivery, self.store, session_ref, queue_id
        )

    async def turn_result(self, session_ref, turn_id):
        await asyncio.to_thread(self._saved_session, session_ref)
        return await asyncio.to_thread(self.store.turn_result, session_ref, turn_id)

    async def launch(self, prepared, *, launcher=None, operation_id, process=None):
        operation_key(operation_id)
        if isinstance(prepared, dict):
            prepared = PreparedLaunch.from_dict(prepared)
        from .runtime import launcher as resolve_launcher, process_options

        from .config import process_defaults

        process = (
            process_defaults(self, process, operation_id)
            if launcher is None
            else process_options(process)
        )
        if launcher is not None and process:
            raise ConversationError(
                "validation", "Process options require a built-in runtime launcher"
            )
        portable = prepared.to_dict()
        connection_id = (
            prepared.connection_id
            or uuid.uuid5(
                uuid.NAMESPACE_URL, self.store.store_id + ":" + operation_id
            ).hex
        )
        intent = {"method": "launch", "prepared": portable}
        if launcher is None and (self.runtime["kind"] != "standalone" or process):
            intent["runtime"] = deepcopy(self.runtime)
            intent["process"] = process
        row, fresh = self.store.claim(operation_id, intent)
        if not fresh:
            return OperationReceipt(row)
        allocated = replace(prepared, connection_id=connection_id)
        try:
            self.store.reserve_connection(connection_id, allocated.to_dict())
        except ConversationError as exc:
            return self.store.finish(
                receipt(operation_id, "failed", error=exc.to_dict())
            )
        if launcher is None:
            self.store.put(
                "launch-policy",
                connection_id,
                dict(runtime=self.runtime, process=process),
            )
            launcher = resolve_launcher(self, process=process, prepared=allocated)

        async def launch_once():
            try:
                launched = await launcher.launch(allocated, operation_id=operation_id)
                ref = launched.get("connection_ref")
                if ref and (
                    ref.get("connection_id") != connection_id
                    or ref.get("backend_id") != prepared.backend_id
                    or ref.get("adapter_id") != prepared.adapter_id
                    or ref.get("protocol") != prepared.protocol
                    or ref.get("runtime_ref") != launched.get("runtime_ref")
                ):
                    raise ConversationError(
                        "uncertain",
                        "Launcher returned conflicting connection/runtime identity",
                        uncertain=True,
                        recovery={"launch_receipt": launched},
                    )
                if ref:
                    prior = self.store.get("connection", connection_id) or {}
                    self.store.put(
                        "connection", connection_id, {**prior, "connection_ref": ref}
                    )
                return self.store.finish(
                    receipt(
                        operation_id,
                        launched["status"],
                        result={"connection_ref": ref},
                        error=launched.get("error"),
                        recovery={
                            **launched.get("recovery", {}),
                            "connection_ref": ref,
                            "runtime_ref": launched.get("runtime_ref"),
                        },
                    )
                )
            except Exception as exc:
                err = (
                    exc
                    if isinstance(exc, ConversationError)
                    else ConversationError(
                        "uncertain",
                        "Launch result unknown; inspect without replay",
                        uncertain=True,
                    )
                )
                return self.store.finish(
                    receipt(
                        operation_id,
                        "unsupported"
                        if err.code == "unsupported"
                        else "uncertain"
                        if err.uncertain
                        else "failed",
                        error=err.to_dict(),
                        recovery={
                            **self.store.operation(operation_id)["recovery"],
                            **err.recovery,
                        },
                    )
                )

        task = asyncio.create_task(launch_once())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return OperationReceipt(await asyncio.shield(task))

    async def launch_codex(
        self,
        environment_id,
        *,
        cwd,
        agents=None,
        settings=None,
        operation_id,
        process=None,
    ):
        """Launch local Codex using a guarded public Agent Manager environment."""
        return await self._launch_native_environment(
            "codex",
            environment_id,
            cwd=cwd,
            agents=agents,
            settings=settings,
            operation_id=operation_id,
            process=process,
        )

    async def launch_opencode(
        self,
        environment_id,
        *,
        cwd,
        agents=None,
        settings=None,
        operation_id,
        process=None,
    ):
        """Launch a retained OpenCode worker using its selected native environment."""
        return await self._launch_native_environment(
            "opencode",
            environment_id,
            cwd=cwd,
            agents=agents,
            settings=settings,
            operation_id=operation_id,
            process=process,
        )

    async def _launch_native_environment(
        self,
        provider,
        environment_id,
        *,
        cwd,
        agents,
        settings,
        operation_id,
        process=None,
    ):
        from pathlib import Path
        from .agents import AgentEnvironments
        from .models import digest, reject_credentials, canonical

        agents = agents or AgentEnvironments(provider)
        operation_key(operation_id)
        if settings is not None and not isinstance(settings, dict):
            raise ConversationError(
                "validation", "Native conversation settings must be a mapping"
            )
        directory = str(Path(cwd).expanduser().resolve())
        intent = dict(
            environment_id=environment_id,
            cwd=directory,
            settings=deepcopy(settings or {}),
            agents=agents.identity,
        )
        reject_credentials(intent)
        canonical(intent)
        preparation_id = provider + "-prepare:" + digest(operation_id)[7:]
        row, fresh = self.store.claim(preparation_id, intent)
        if fresh:
            try:
                if not Path(directory).is_dir():
                    raise ConversationError(
                        "validation", "Conversation working directory is unavailable"
                    )
                descriptor = await asyncio.to_thread(
                    agents.prepare_native, environment_id
                )
                selected = deepcopy(intent["settings"])
                if descriptor.get("model") and "model" not in selected:
                    selected["model"] = descriptor["model"]
                if descriptor["provider"] != provider:
                    raise ConversationError(
                        "conflict", "Selected environment belongs to another provider"
                    )
                codex = provider == "codex"
                prepared = PreparedLaunch(
                    backend_id=provider,
                    adapter_id="native-codex" if codex else "opencode-native",
                    argv=[] if codex else [descriptor["executable"]],
                    protocol="codex-app-server" if codex else "opencode-v2",
                    cwd=directory,
                    settings=selected,
                    transport={
                        "kind": "managed-codex" if codex else "opencode-http",
                        "agent_environment_ref": descriptor,
                    },
                )
                row = self.store.finish(
                    receipt(
                        preparation_id,
                        "completed",
                        result={"prepared": prepared.to_dict()},
                    )
                )
            except Exception as exc:
                error = (
                    exc
                    if isinstance(exc, ConversationError)
                    else ConversationError("preparation", str(exc))
                )
                row = self.store.finish(
                    receipt(preparation_id, "failed", error=error.to_dict())
                )
        if row["status"] != "completed":
            raise ConversationError(
                "preparation",
                "Native environment preparation did not complete; inspect the preparation receipt",
                uncertain=row["status"] in ("accepted", "uncertain"),
                operation_id=operation_id,
                recovery={"preparation_id": preparation_id, "receipt": dict(row)},
            )
        return await self.launch(
            PreparedLaunch.from_dict(row["result"]["prepared"]),
            operation_id=operation_id,
            process=process,
        )

    async def connect(self, connection_ref, *, connector=None):
        if (
            not isinstance(connection_ref, dict)
            or connection_ref.get("schema") != "conversation-manager/connection-ref-1"
        ):
            raise ConversationError("validation", "Invalid connection reference")
        connector = connector or LocalConnector()
        handle = await connector.attach(connection_ref)
        return Connection(self, ConnectionRef(connection_ref), connector, handle)

    async def operation(self, operation_id):
        row = self.store.operation(operation_id)
        # Accepted work whose worker vanished cannot be inferred complete or replayed.
        ref = row.get("recovery", {}).get("connection_ref")
        if (
            row["status"] == "accepted"
            and ref
            and ref.get("transport", {}).get("kind") == "conversation-manager-local"
        ):
            try:
                handle = await LocalConnector(timeout=1).attach(ref)
                await handle.close()
            except ConversationError:
                row = self.store.finish(
                    receipt(
                        operation_id,
                        "uncertain",
                        result=row["result"],
                        recovery=row["recovery"],
                        error=ConversationError(
                            "uncertain",
                            "Retained worker unavailable; operation outcome unconfirmed",
                            uncertain=True,
                            operation_id=operation_id,
                            recovery=row["recovery"],
                        ).to_dict(),
                    )
                )
        return OperationReceipt(row)


class Connection:
    def __init__(self, client, ref, connector, handle):
        self.client, self.ref, self.connector, self.handle = (
            client,
            ref,
            connector,
            handle,
        )

    async def _call(self, method, params):
        return await self.handle.call(method, params)

    async def capabilities(self):
        return await self._call("capabilities", {})

    async def create_session(self, settings, *, operation_id):
        return OperationReceipt(
            await self._call(
                "create_session", {"settings": settings, "operation_id": operation_id}
            )
        )

    async def create_conversation(self, settings=None, *, operation_id):
        result = await self.create_session(
            {} if settings is None else settings, operation_id=operation_id
        )
        return self._conversation_result(result)

    async def load_conversation(self, reference, *, operation_id):
        result = await self.load_session(reference, operation_id=operation_id)
        return self._conversation_result(result)

    async def attach_conversation(self, reference):
        """Bind a retained conversation, including one with an active turn."""
        from .conversation import Conversation

        reference = deepcopy(reference)
        await self.session_state(reference)
        return Conversation(self, reference)

    def _conversation_result(self, result):
        from .conversation import Conversation

        if result["status"] != "completed":
            error = result.get("error") or {}
            raise ConversationError(
                error.get("code", "operation_incomplete"),
                error.get("message", "Conversation operation did not complete"),
                uncertain=result["status"] in ("accepted", "uncertain"),
                operation_id=result["operation_id"],
                recovery={"receipt": dict(result)},
            )
        return Conversation(
            self, result["result"]["session_ref"], creation_receipt=result
        )

    async def assess_resources(self):
        return await self._call("assess_resources", {})

    async def finalize_resources(
        self, selected, resource_snapshot_ref, *, operation_id
    ):
        return OperationReceipt(
            await self._call(
                "finalize_resources",
                {
                    "selected": selected,
                    "resource_snapshot_ref": resource_snapshot_ref,
                    "operation_id": operation_id,
                },
            )
        )

    async def load_session(self, session_ref, *, operation_id):
        return OperationReceipt(
            await self._call(
                "load_session",
                {"session_ref": session_ref, "operation_id": operation_id},
            )
        )

    async def restore_session(self, saved_session_ref, *, launcher=None, operation_id):
        """Explicit replacement connection; inspect the previous owner before dispatch."""
        operation_key(operation_id)
        saved = self.client.store.get("session", saved_session_ref.get("session_id"))
        if not saved or saved["session_ref"] != saved_session_ref:
            raise ConversationError(
                "conflict", "Restore requires an exact recorded session"
            )
        prior_ref = saved_session_ref["recovery"]["connection_ref"]
        if prior_ref == self.ref:
            raise ConversationError(
                "conflict", "Use load_session for the current connection"
            )
        # Reusing a completed restore inspects its receipt, without starting SDKs.
        authorization = {
            "saved_session_ref": saved_session_ref,
            "connection_ref": dict(self.ref),
        }
        if (
            self.client.store.get("restore-authorization", operation_id)
            == authorization
        ):
            try:
                previous = self.client.store.operation(operation_id)
            except ConversationError as error:
                if error.code != "not_found":
                    raise
            else:
                return OperationReceipt(previous)
        if launcher is None:
            from .runtime import launcher as resolve_launcher

            launcher = resolve_launcher(self.client, prior_ref["runtime_ref"])
        observation = await launcher.inspect(prior_ref["runtime_ref"])
        if observation.get("runtime_ref") != prior_ref[
            "runtime_ref"
        ] or observation.get("state") not in ("stopped", "exited", "failed"):
            raise ConversationError(
                "busy", "Previous SDK runtime must be confirmed stopped before restore"
            )
        self.client.store.put("restore-authorization", operation_id, authorization)
        return OperationReceipt(
            await self._call(
                "restore_session",
                {"saved_session_ref": saved_session_ref, "settings": deepcopy(saved["settings"]),
                 "operation_id": operation_id},
            )
        )

    async def send(self, session_ref, content, *, operation_id):
        return OperationReceipt(
            await self._call(
                "send",
                {
                    "session_ref": session_ref,
                    "content": content,
                    "operation_id": operation_id,
                },
            )
        )

    async def queue_state(self, session_ref):
        return await self._call("queue_state", {"session_ref": session_ref})

    async def update_queue(self, session_ref, action, *, operation_id, **params):
        return OperationReceipt(
            await self._call(
                "queue_update",
                {
                    **params,
                    "session_ref": session_ref,
                    "action": action,
                    "operation_id": operation_id,
                },
            )
        )

    async def steer(self, session_ref, text, *, expected_turn_id, operation_id):
        return OperationReceipt(
            await self._call(
                "steer",
                {
                    "session_ref": session_ref,
                    "text": text,
                    "expected_turn_id": expected_turn_id,
                    "operation_id": operation_id,
                },
            )
        )

    async def event_page(self, session_ref=None, *, after_sequence=0, wait=False):
        return await self._call(
            "events",
            {
                "session_ref": session_ref,
                "after_sequence": after_sequence,
                "wait": wait,
            },
        )

    async def events(self, session_ref=None, *, after_sequence=None):
        cursor = 0 if after_sequence is None else after_sequence
        while True:
            page = await self.event_page(session_ref, after_sequence=cursor, wait=True)
            for event in page["events"]:
                yield event
            prior, cursor = cursor, page["cursor"]
            if page["closed"] and cursor == prior:
                return

    async def pending_requests(self, session_ref=None):
        return await self._call("pending_requests", {"session_ref": session_ref})

    async def session_state(self, session_ref):
        return await self._call("session_state", {"session_ref": session_ref})

    async def turn_result(self, session_ref, turn_id):
        return await self._call(
            "turn_result", {"session_ref": session_ref, "turn_id": turn_id}
        )

    async def respond(self, request_id, response, *, operation_id):
        return OperationReceipt(
            await self._call(
                "respond",
                {
                    "request_id": request_id,
                    "response": response,
                    "operation_id": operation_id,
                },
            )
        )

    async def cancel(self, session_ref, turn_id, *, operation_id):
        return OperationReceipt(
            await self._call(
                "cancel",
                {
                    "session_ref": session_ref,
                    "turn_id": turn_id,
                    "operation_id": operation_id,
                },
            )
        )

    async def history(self, session_ref):
        return await self._call("history", {"session_ref": session_ref})

    async def history_page(self, session_ref, *, cursor=0, limit=100):
        return await self._call(
            "history_page",
            {"session_ref": session_ref, "cursor": cursor, "limit": limit},
        )

    async def begin_drain(self, *, operation_id):
        return OperationReceipt(
            await self._call("begin_drain", {"operation_id": operation_id})
        )

    async def drain_status(self):
        return await self._call("drain_status", {})

    async def prepare_stop(self, *, operation_id):
        return OperationReceipt(
            await self._call("prepare_stop", {"operation_id": operation_id})
        )

    async def configuration(self, session_ref):
        """Read configuration options advertised by this exact session."""
        return await self._call("configuration", {"session_ref": session_ref})

    async def commands(self, session_ref):
        """Read this session's latest advertised command snapshot."""
        return await self._call("commands", {"session_ref": session_ref})

    async def list_slash_commands(self, session_ref, *, available_only=False):
        from .slash_commands import catalog

        return catalog(
            await self.commands(session_ref),
            backend_id=self.ref["backend_id"],
            available_only=available_only,
        )

    async def command(self, session_ref, name, arguments="", *, operation_id):
        return OperationReceipt(
            await self._call(
                "execute_command",
                {
                    "session_ref": session_ref,
                    "name": name,
                    "arguments": arguments,
                    "operation_id": operation_id,
                },
            )
        )

    async def set_config(self, session_ref, config_id, value, *, operation_id):
        return OperationReceipt(
            await self._call(
                "set_config",
                {
                    "session_ref": session_ref,
                    "config_id": config_id,
                    "value": value,
                    "operation_id": operation_id,
                },
            )
        )

    async def fork(self, session_ref, *, operation_id):
        return OperationReceipt(
            await self._call(
                "fork", {"session_ref": session_ref, "operation_id": operation_id}
            )
        )

    async def close_session(self, session_ref, *, operation_id):
        return OperationReceipt(
            await self._call(
                "close_session",
                {"session_ref": session_ref, "operation_id": operation_id},
            )
        )

    async def set_model(self, session_ref, model, *, operation_id):
        return OperationReceipt(
            await self._call(
                "set_model",
                {
                    "session_ref": session_ref,
                    "model": model,
                    "operation_id": operation_id,
                },
            )
        )

    async def attach_terminal(self, session_ref, *, connector, operation_id):
        return OperationReceipt(
            await self._call(
                "attach_terminal",
                {"session_ref": session_ref, "operation_id": operation_id},
            )
        )

    async def mcp_target(self, session_ref):
        """Return the exact retained MCP target without selecting or launching anything."""
        saved = await asyncio.to_thread(self.client._saved_session, session_ref)
        if (
            session_ref["connection_id"] != self.ref["connection_id"]
            or session_ref["recovery"]["connection_ref"] != self.ref
        ):
            raise ConversationError(
                "conflict", "MCP session belongs to a different connection"
            )
        mcp = saved["settings"].get("mcp") or {}
        return dict(
            schema="mcp-manager/runtime-target-1",
            connector_id="conversation-manager",
            backend_id=self.ref["backend_id"],
            connection_ref=deepcopy(self.ref),
            runtime_ref=deepcopy(self.ref["runtime_ref"]),
            native_session_id=session_ref["native_session_id"],
            selection_ref=deepcopy(mcp.get("selection_ref")),
            selection_digest=mcp.get("selection_digest"),
            prepared_generations=deepcopy(mcp.get("prepared_generations", [])),
        )

    def mcp_connector(self):
        from .mcp import LiveMcpConnector

        return LiveMcpConnector(self)

    async def disconnect(self):
        await self.handle.close()

    async def reconnect(self):
        self.handle = await self.connector.attach(self.ref)
        return self
