"""Worker-owned sessions, tasks, requests and normalized outcomes."""

from __future__ import annotations
import asyncio
import inspect
import uuid
from pathlib import Path

from .models import canonical, receipt, redact, reject_credentials, ConversationError
from .store import now


class Core:
    operation_names = (
        "create_session",
        "load_session",
        "resume",
        "send",
        "events",
        "cancel",
        "permissions",
        "history",
        "fork",
        "close_session",
        "set_model",
        "user_input",
        "attach_terminal",
        "mcp_inventory",
        "mcp_reconnect",
        "instructions_delivery",
        "skills_delivery",
        "mcp_delivery",
        "access_delivery",
        "filesystem_callbacks",
        "terminal_callbacks",
        "set_config",
        "execute_command",
        "restore_session",
        "session_state",
        "turn_result",
        "steer",
        "queue_update",
    )

    def __init__(self, store, prepared, ref, *, services=None):
        self.store, self.prepared, self.ref = store, prepared, ref
        from .message_queue import MessageQueue

        self.message_queue = MessageQueue(self)
        self.epoch = uuid.uuid4().hex
        self.sessions, self.active, self.tasks, self.requests, self.inflight = (
            {},
            {},
            set(),
            {},
            set(),
        )
        self.changed, self.shutdown = asyncio.Event(), asyncio.Event()
        self.lost = False
        self.resource_lock = asyncio.Lock()
        self.configuring = set()
        finalized = store.get("resource-finalization", ref["connection_id"])
        if finalized:
            prepared.settings = finalized["settings"]
            prepared.resource_snapshot_ref = finalized["resource_snapshot_ref"]
        self.secrets = [
            v
            for k, v in prepared.env.items()
            if any(t in k.lower() for t in ("key", "token", "secret", "password"))
        ]
        self.retention = int(prepared.transport.get("event_retention", 10000))
        if not 1 <= self.retention <= 1000000:
            raise ConversationError(
                "validation", "event_retention must be between 1 and 1000000"
            )
        from .adapters.registry import adapter

        self.adapter = adapter(self, prepared, services)
        self.draining = bool(store.get("worker-drain", ref["connection_id"]))

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def emit(
        self,
        kind,
        payload,
        native_session=None,
        *,
        native=None,
        receipts=(),
        artifact=None,
        turn_ref=None,
    ):
        session = next(
            (
                s
                for s in self.sessions.values()
                if s["native_session_id"] == native_session
            ),
            None,
        )
        turn = turn_ref or (self.active.get(session["session_id"]) if session else None)
        event = self.store.append(
            self.ref,
            self.epoch,
            kind,
            redact(payload, self.secrets),
            session=session,
            turn=turn,
            native=redact(native, self.secrets),
            native_session_id=native_session,
            retention=self.retention,
            receipts=redact(list(receipts), self.secrets),
            artifact=redact(artifact, self.secrets),
        )
        self.message_queue.observe(session, turn, kind, payload, native)
        self.changed.set()
        return event

    def capabilities(self):
        return {
            "schema": "conversation-manager/capabilities-1",
            "backend_id": self.ref["backend_id"],
            "adapter_id": self.ref["adapter_id"],
            "adapter_version": self.adapter.adapter_version,
            "protocol": self.prepared.protocol,
            "protocol_version": self.adapter.protocol_version,
            "raw": redact(self.adapter.raw, self.secrets),
            "client_services": getattr(self.adapter, "client_caps", {}),
            "operations": {
                **self.adapter.operations,
                "queue_update": {"supported": True, "scope": "session", "reason": None},
            },
            "queue_modes": self.message_queue.modes(),
            "queue_revision": 1,
            "resource_policy_version": "conversation-manager/resource-policy-1",
        }

    def session(self, ref):
        if (
            not isinstance(ref, dict)
            or ref.get("schema") != "conversation-manager/session-ref-1"
        ):
            raise ConversationError("validation", "Invalid session reference")
        row = self.sessions.get(ref.get("session_id"))
        if row != ref:
            raise ConversationError(
                "conflict",
                "Session reference does not exactly identify this connection's session",
            )
        return row

    def error(self, exc, operation_id, *, dispatched):
        if isinstance(exc, ConversationError):
            return ConversationError(
                exc.code,
                redact(exc.message, self.secrets),
                uncertain=exc.uncertain,
                operation_id=operation_id,
                recovery={
                    "connection_ref": self.ref,
                    **redact(exc.recovery, self.secrets),
                },
            )
        return ConversationError(
            "transport" if dispatched else "validation",
            "Backend delivery or completion is unconfirmed; inspect without replay"
            if dispatched
            else "Backend rejected the operation",
            uncertain=dispatched,
            operation_id=operation_id,
            recovery={"connection_ref": self.ref},
        )

    def finish_error(self, operation_id, error, result=None):
        status = (
            "unsupported"
            if error.code == "unsupported"
            else "uncertain"
            if error.uncertain
            else "failed"
        )
        try:
            prior = self.store.operation(operation_id)
        except Exception:
            prior = {"recovery": {}, "result": None}
        recovery = {**prior["recovery"], **error.recovery, "connection_ref": self.ref}
        row = receipt(
            operation_id,
            status,
            result=result if result is not None else prior["result"],
            error={**error.to_dict(), "recovery": recovery},
            recovery=recovery,
        )
        try:
            return self.store.finish(row)
        except Exception:
            # Surface all known identities even when durable receipt storage is
            # unavailable. The retained intent/checkpoint still prevents replay.
            error = ConversationError(
                "io",
                "Operation receipt could not be persisted; inspect without replay",
                uncertain=True,
                operation_id=operation_id,
                recovery=recovery,
            )
            return receipt(
                operation_id,
                "uncertain",
                result=row["result"],
                error=error.to_dict(),
                recovery=recovery,
            )

    def supported(self, name):
        if not self.adapter.operations.get(name, {}).get("supported"):
            raise ConversationError(
                "unsupported", f"{name} is not supported by this negotiated adapter"
            )

    async def mutate(self, method, params):
        operation_id = params["operation_id"]
        if method == "send" and isinstance(params.get("content"), str):
            params = {
                **params,
                "content": [{"type": "text", "text": params["content"]}],
            }
        reject_credentials(params)
        intent = {
            "connection_id": self.ref["connection_id"],
            "method": method,
            "params": {k: v for k, v in params.items() if k != "operation_id"},
        }
        row, fresh = self.store.claim(
            operation_id, intent, {"connection_ref": self.ref}
        )
        if not fresh:
            return row
        self.inflight.add(operation_id)
        dispatched = False
        configuring = None
        controls_confirmed = False
        known_result, known_recovery = None, {}
        try:
            if self.lost:
                raise ConversationError(
                    "transport",
                    "Backend transport is closed; exact reconnect only attaches the journal",
                )
            if self.draining and method not in ("respond", "cancel", "close_session"):
                raise ConversationError(
                    "auth_recovery_pending", "Worker is draining; new work is closed"
                )
            if method == "queue_update":
                session = self.session(params["session_ref"])
                value = self.message_queue.mutate(session, params)
                result = self.store.finish(
                    receipt(operation_id, "completed", result=value)
                )
                if params["action"] == "resume":
                    self.spawn(self.message_queue.deliver(session))
                return result
            if method == "steer":
                self.supported("steer")
                session = self.session(params["session_ref"])
                turn = self.active.get(session["session_id"])
                if not turn or turn["turn_id"] != params.get("expected_turn_id"):
                    raise ConversationError(
                        "conflict", "The targeted turn is no longer active"
                    )
                if turn["cancel_operations"]:
                    raise ConversationError("conflict", "The targeted turn is stopping")
                text = params.get("text")
                if not isinstance(text, str) or not text.strip() or len(text) > 32000:
                    raise ConversationError(
                        "validation",
                        "Steering requires nonempty text of at most 32000 characters",
                    )
                known_result = {
                    "turn_id": turn["turn_id"],
                    "input_id": operation_id,
                    "delivery": "steer",
                }
                known_recovery = {"connection_ref": self.ref, "session_ref": session}
                dispatched = True
                await self.adapter.steer(session, text, turn, input_id=operation_id)
                # A successful stream write confirms handoff, not model consumption.
                # Publish the input and its receipt together; an interrupted write
                # retains the original uncertain intent and must never be replayed.
                completed = receipt(
                    operation_id,
                    "completed",
                    result=known_result,
                    recovery=known_recovery,
                )
                self.emit(
                    "message.input",
                    {
                        "input_id": operation_id,
                        "text": text,
                        "delivery": "steer",
                        **(
                            {
                                "queue_id": params["queue_id"],
                                "delivery_mode": params["delivery_mode"],
                            }
                            if params.get("queue_id")
                            else {}
                        ),
                    },
                    session["native_session_id"],
                    receipts=[completed],
                    turn_ref=turn,
                )
                return completed
            if method == "finalize_resources":
                from .resource_finalization import finalize

                value = await finalize(self, params)
                return self.store.finish(
                    receipt(
                        operation_id,
                        "completed",
                        result=value,
                        recovery={"connection_ref": self.ref},
                    )
                )
            if method in ("create_session", "load_session", "restore_session"):
                if self.prepared.resource_policy is not None and not self.store.get(
                    "resource-finalization", self.ref["connection_id"]
                ):
                    raise ConversationError(
                        "conflict",
                        "Finalize captured resources before creating a session",
                    )
                if self.prepared.resource_policy is not None and params.get("settings"):
                    if any(
                        self.prepared.settings.get(k) != v
                        for k, v in params["settings"].items()
                    ):
                        raise ConversationError(
                            "conflict", "Finalized session settings are immutable"
                        )
                settings = {**self.prepared.settings, **params.get("settings", {})}
                settings["cwd"] = str(
                    Path(settings.get("cwd", self.prepared.cwd)).expanduser().resolve()
                )
                session = (
                    self.session(params["session_ref"])
                    if method == "load_session"
                    else None
                )
                saved_session = None
                if method == "restore_session":
                    self.supported("restore_session")
                    saved_session = params["saved_session_ref"]
                    saved = self.store.get("session", saved_session.get("session_id"))
                    authorization = self.store.get(
                        "restore-authorization", operation_id
                    )
                    if (
                        not saved
                        or saved["session_ref"] != saved_session
                        or authorization
                        != {
                            "saved_session_ref": saved_session,
                            "connection_ref": self.ref,
                        }
                    ):
                        raise ConversationError(
                            "conflict",
                            "Restore requires an exact saved session and inspected stopped runtime",
                        )
                    if (
                        saved_session["adapter_id"] != self.ref["adapter_id"]
                        or saved_session["backend_id"] != self.ref["backend_id"]
                    ):
                        raise ConversationError(
                            "conflict", "Restore backend differs from the saved session"
                        )
                    if (
                        settings != saved["settings"]
                        or self.prepared.resource_snapshot_ref
                        != saved_session["resource_snapshot_ref"]
                    ):
                        raise ConversationError(
                            "conflict",
                            "Restore must preserve captured resources and confirmed settings",
                        )
                if session:
                    if session["session_id"] in self.active:
                        raise ConversationError(
                            "busy", "Cannot load a session while its turn is active"
                        )
                    settings = self.store.get("session", session["session_id"])[
                        "settings"
                    ]
                    if not (
                        self.adapter.operations["load_session"]["supported"]
                        or self.adapter.operations["resume"]["supported"]
                    ):
                        self.supported("load_session")
                else:
                    self.supported(
                        "restore_session" if saved_session else "create_session"
                    )
                self.adapter.validate_settings(settings)
                dispatched = True
                if session:
                    native = await self.adapter.load(session, settings)
                else:
                    if saved_session:
                        native_id, native = await self.adapter.restore(
                            saved_session, settings
                        )
                    elif getattr(self.adapter, "sdk_runtime", False) or getattr(
                        self.adapter, "keyed_sessions", False
                    ):
                        native_id, native = await self.adapter.create(
                            settings, operation_id=operation_id
                        )
                    else:
                        native_id, native = await self.adapter.create(settings)
                    if not isinstance(native_id, str) or not native_id:
                        raise ConversationError(
                            "uncertain",
                            "Backend returned an invalid session identity",
                            uncertain=True,
                        )
                    session = dict(
                        schema="conversation-manager/session-ref-1",
                        session_id=uuid.uuid4().hex,
                        connection_id=self.ref["connection_id"],
                        backend_id=self.ref["backend_id"],
                        adapter_id=self.ref["adapter_id"],
                        protocol_version=self.adapter.protocol_version,
                        native_session_id=native_id,
                        cwd=settings["cwd"],
                        resource_snapshot_ref=self.prepared.resource_snapshot_ref,
                        recovery={"connection_ref": self.ref},
                    )
                    if saved_session:
                        session["recovery"]["restored_from"] = saved_session
                    if native.get("identity_evidence"):
                        session["identity_evidence_at_creation"] = native[
                            "identity_evidence"
                        ]
                    self.sessions[session["session_id"]] = session
                    known_result = {
                        "session_ref": session,
                        "native": redact(native, self.secrets),
                    }
                    known_recovery = {
                        "connection_ref": self.ref,
                        "runtime_ref": self.ref["runtime_ref"],
                        "session_ref": session,
                        "native_session_id": native_id,
                    }
                    self.store.put(
                        "operation-recovery",
                        operation_id,
                        {"result": known_result, "recovery": known_recovery},
                    )
                    self.store.put(
                        "session",
                        session["session_id"],
                        {
                            "session_ref": session,
                            "settings": settings,
                            **(
                                {"native_settings": saved["native_settings"]}
                                if saved_session and saved.get("native_settings")
                                else {}
                            ),
                        },
                    )
                    if saved_session and self.ref["protocol"] == "codex-app-server":
                        thread = native.get("thread") or {}
                        state = thread.get("status") or {}
                        active = (
                            state == "active"
                            or isinstance(state, dict)
                            and state.get("type") == "active"
                        )
                        turns = thread.get("turns") or []
                        if active and turns and turns[-1].get("status") == "inProgress":
                            self.external_turn(native_id, turns[-1])
                return self.store.finish(
                    receipt(
                        operation_id,
                        "completed",
                        result={
                            "session_ref": session,
                            "native": redact(native, self.secrets),
                        },
                        recovery={"connection_ref": self.ref, **known_recovery},
                    )
                )
            if (
                method == "send"
                or method == "execute_command"
                and getattr(self.adapter, "is_turn_command", lambda *_: False)(
                    params["name"], params.get("arguments", "")
                )
            ):
                self.supported(method)
                session = self.session(params["session_ref"])
                if (
                    session["session_id"] in self.active
                    or session["session_id"] in self.configuring
                ):
                    raise ConversationError(
                        "busy",
                        "A turn or settings update is already running for this session",
                    )
                if hasattr(self.adapter, "validate_send"):
                    self.adapter.validate_send(session)
                content = params.get("content")
                if method == "send":
                    if isinstance(content, str):
                        content = [{"type": "text", "text": content}]
                    if (
                        not isinstance(content, list)
                        or not content
                        or any(
                            not isinstance(b, dict)
                            or set(b) != {"type", "text"}
                            or b["type"] != "text"
                            or not isinstance(b["text"], str)
                            for b in content
                        )
                    ):
                        raise ConversationError(
                            "unsupported", "This adapter accepts UTF-8 text blocks only"
                        )
                else:
                    self.adapter.validate_command(
                        params["name"], params.get("arguments", "")
                    )
                    if hasattr(self.adapter, "validate_session_command"):
                        self.adapter.validate_session_command(
                            session, params["name"], params.get("arguments", "")
                        )
                canonical(content)
                if method == "send" and hasattr(self.adapter, "validate_content"):
                    self.adapter.validate_content(session, content)
                turn = {
                    "turn_id": uuid.uuid4().hex,
                    "native_turn_id": None,
                    "operation_id": operation_id,
                    "cancel_operations": [],
                }
                self.active[session["session_id"]] = turn
                result = self.store.finish(
                    receipt(
                        operation_id,
                        "accepted",
                        result={"turn_id": turn["turn_id"], "native_turn_id": None},
                        recovery={"connection_ref": self.ref, "session_ref": session},
                    )
                )
                self.spawn(
                    self.prompt(
                        session,
                        content,
                        turn,
                        command=params if method == "execute_command" else None,
                    )
                )
                return result
            if method == "respond":
                return await self.respond(params)
            if method == "cancel":
                self.supported("cancel")
                session = self.session(params["session_ref"])
                turn = self.active.get(session["session_id"])
                if not turn or turn["turn_id"] != params["turn_id"]:
                    raise ConversationError(
                        "conflict",
                        "Turn is no longer active or does not belong to this session",
                    )
                dispatched = True
                self.message_queue.pause(
                    session,
                    "Cancellation requested. Resume the queue explicitly after the turn stops.",
                )
                turn["cancel_operations"].append(operation_id)
                await self.adapter.cancel(session, turn)
                current = self.store.operation(operation_id)
                if current["status"] == "completed":
                    return current
                return self.store.finish(
                    receipt(
                        operation_id,
                        "accepted",
                        result={
                            "turn_id": turn["turn_id"],
                            "cancellation_observed": False,
                        },
                        recovery={"connection_ref": self.ref, "session_ref": session},
                    )
                )
            self.supported(method)
            session = self.session(params["session_ref"])
            if session["session_id"] in self.configuring:
                raise ConversationError(
                    "busy", "A session settings update is already running"
                )
            if method == "set_model" and session["session_id"] in self.active:
                raise ConversationError(
                    "busy", "Wait for the active turn before changing the model"
                )
            if method == "set_config":
                if session["session_id"] in self.active:
                    raise ConversationError(
                        "busy",
                        "Wait for the active turn before changing session configuration",
                    )
                self.adapter.validate_config(
                    session, params["config_id"], params["value"]
                )
            if method == "execute_command":
                if session[
                    "session_id"
                ] in self.active and self.adapter.command_requires_idle(
                    params["name"], params.get("arguments", "")
                ):
                    raise ConversationError(
                        "busy", "Wait for the active turn before running a command"
                    )
                self.adapter.validate_command(
                    params["name"], params.get("arguments", "")
                )
            if (
                method in ("set_config", "set_model")
                or method == "execute_command"
                and getattr(self.adapter, "is_setting_command", lambda *_: False)(
                    params["name"]
                )
            ):
                configuring = session["session_id"]
                self.configuring.add(configuring)
            dispatched = True
            value = await self.adapter.optional(method, session, params)
            finished = self.store.finish(
                receipt(
                    operation_id,
                    "completed",
                    result=value,
                    recovery={"connection_ref": self.ref},
                )
            )
            controls_confirmed = True
            return finished
        except Exception as exc:
            error = self.error(exc, operation_id, dispatched=dispatched)
            error.recovery.update(known_recovery)
            return self.finish_error(operation_id, error, known_result)
        finally:
            if configuring is not None:
                self.configuring.discard(configuring)
                if not self.lost and controls_confirmed:
                    self.spawn(self.message_queue.deliver(session))
                elif not controls_confirmed:
                    try:
                        if any(
                            item["status"] == "queued"
                            for item in self.message_queue.get(session)["items"]
                        ):
                            self.message_queue.pause(
                                session, "settings-update-unconfirmed"
                            )
                    except Exception:
                        pass  # Preserve the original mutation receipt if queue IO also fails.
            self.inflight.discard(operation_id)

    async def prompt(self, session, content, turn, *, command=None):
        operation_id = turn["operation_id"]
        status = None
        try:
            result = (
                await self.adapter.command_prompt(session, command, turn)
                if command
                else await self.adapter.prompt(session, content, turn)
            )
            stop = result["stop_reason"]
            status = result.get("status") or (
                "cancelled"
                if stop == "cancelled"
                else "completed"
                if stop in ("end_turn", "completed")
                else "failed"
                if stop
                in ("error", "failed", "refusal", "max_tokens", "max_turn_requests")
                else None
            )
            if status not in ("completed", "cancelled", "failed"):
                raise ConversationError(
                    "uncertain", "Unknown backend terminal outcome", uncertain=True
                )
            payload = {
                "status": status,
                "stop_reason": stop,
                "error": result.get("error"),
            }
            result_row = {
                "turn_id": turn["turn_id"],
                "native_turn_id": turn.get("native_turn_id"),
                **payload,
            }
            text = result.get("final_text")
            artifact = {
                "schema": "conversation-manager/turn-result-1",
                "session_key": self.store.history_key(session),
                "turn_id": turn["turn_id"],
                "operation_id": operation_id,
                **payload,
                "final_text": text[:250000] if isinstance(text, str) else None,
                "truncated": bool(result.get("truncated"))
                or isinstance(text, str)
                and len(text) > 250000,
                "source": "sdk-observed"
                if getattr(self.adapter, "sdk_runtime", False)
                else "manager-observed",
            }
            result_row["turn_result_ref"] = (
                self.store.history_key(session) + ":" + turn["turn_id"]
            )
            error = (
                None
                if status == "completed"
                else ConversationError(
                    status, "Turn " + status, operation_id=operation_id
                ).to_dict()
            )
            finished = receipt(
                operation_id,
                "completed" if status == "completed" else "failed",
                result=result_row,
                error=error,
                recovery={"connection_ref": self.ref, "session_ref": session},
            )
            self.emit(
                "turn.completed",
                payload,
                session["native_session_id"],
                native=result.get("native"),
                receipts=[finished],
                artifact=artifact,
            )
            for cancel_id in turn["cancel_operations"]:
                self.store.finish(
                    receipt(
                        cancel_id,
                        "completed" if status == "cancelled" else "failed",
                        result={
                            "turn_id": turn["turn_id"],
                            "cancellation_observed": status == "cancelled",
                        },
                        error=None
                        if status == "cancelled"
                        else ConversationError(
                            "conflict", "Turn ended without observed cancellation"
                        ).to_dict(),
                    )
                )
        except Exception as exc:
            error = self.error(exc, operation_id, dispatched=True)
            sdk_terminal = not error.uncertain
            if not sdk_terminal:
                self.finish_error(
                    operation_id,
                    error,
                    {
                        "turn_id": turn["turn_id"],
                        "native_turn_id": turn.get("native_turn_id"),
                    },
                )
            self.emit(
                "error",
                {
                    "reason": error.message,
                    "affected_operation_ids": [operation_id],
                    "recovery": error.recovery,
                },
                session["native_session_id"],
            )
            if not error.uncertain:
                payload = {
                    "status": "failed",
                    "stop_reason": "error",
                    "error": error.to_dict(),
                }
                artifact, receipts = None, []
                if sdk_terminal:
                    artifact = {
                        "schema": "conversation-manager/turn-result-1",
                        "session_key": self.store.history_key(session),
                        "turn_id": turn["turn_id"],
                        "operation_id": operation_id,
                        **payload,
                        "final_text": None,
                        "truncated": False,
                        "source": "sdk-observed"
                        if getattr(self.adapter, "sdk_runtime", False)
                        else "manager-observed",
                    }
                    receipts = [
                        receipt(
                            operation_id,
                            "failed",
                            error=error.to_dict(),
                            recovery={
                                "connection_ref": self.ref,
                                "session_ref": session,
                            },
                            result={
                                "turn_id": turn["turn_id"],
                                "native_turn_id": turn.get("native_turn_id"),
                                **payload,
                                "turn_result_ref": self.store.history_key(session)
                                + ":"
                                + turn["turn_id"],
                            },
                        )
                    ]
                self.emit(
                    "turn.completed",
                    payload,
                    session["native_session_id"],
                    receipts=receipts,
                    artifact=artifact,
                )
            for cancel_id in turn["cancel_operations"]:
                self.finish_error(
                    cancel_id,
                    ConversationError(
                        "uncertain",
                        "Cancellation outcome is unconfirmed",
                        uncertain=True,
                    ),
                )
        finally:
            for request_id, live in list(self.requests.items()):
                if (
                    live["record"]["session_id"] == session["session_id"]
                    and live["record"]["state"] == "pending"
                ):
                    live["record"]["state"] = "stale"
                    self.store.put("request", request_id, live["record"])
                    if not live["future"].done():
                        live["future"].cancel()
                elif (
                    live["record"]["session_id"] == session["session_id"]
                    and live["record"]["state"] == "submitted"
                ):
                    if locals().get("status") == "completed":
                        self.response_observed(live["record"]["native_request_id"])
                    else:
                        live["record"]["state"] = "uncertain"
                        self.store.put("request", request_id, live["record"])
                        self.finish_error(
                            live["operation_id"],
                            ConversationError(
                                "uncertain",
                                "SDK answer continuation was not observed",
                                uncertain=True,
                            ),
                        )
            self.active.pop(session["session_id"], None)
            self.spawn(self.message_queue.finished(session, status))

    def external_turn(self, native_session, native_turn, *, completed=False):
        """Track provider-owned continuations without inventing a send receipt."""
        session = next(
            (
                row
                for row in self.sessions.values()
                if row["native_session_id"] == native_session
            ),
            None,
        )
        if not session:
            return
        current = self.active.get(session["session_id"])
        if not completed:
            if current:
                return
            current = dict(
                turn_id=uuid.uuid4().hex,
                native_turn_id=native_turn["id"],
                operation_id=None,
                cancel_operations=[],
            )
            self.active[session["session_id"]] = current
            self.emit(
                "session.status",
                {"turn_id": current["turn_id"], "status": "running"},
                native_session,
            )
        elif (
            current
            and current["operation_id"] is None
            and current["native_turn_id"] == native_turn["id"]
        ):
            status = (
                "cancelled"
                if native_turn["status"] == "interrupted"
                else native_turn["status"]
            )
            self.emit(
                "turn.completed",
                {"status": status, "error": native_turn.get("error")},
                native_session,
                native=native_turn,
            )
            for operation_id in current["cancel_operations"]:
                self.store.finish(
                    receipt(
                        operation_id,
                        "completed" if status == "cancelled" else "failed",
                        result={
                            "turn_id": current["turn_id"],
                            "cancellation_observed": status == "cancelled",
                        },
                    )
                )
            for live in self.requests.values():
                if (
                    live["record"]["session_id"] == session["session_id"]
                    and live["record"]["state"] == "pending"
                ):
                    live["record"]["state"] = "stale"
                    self.store.put(
                        "request", live["record"]["request_id"], live["record"]
                    )
                    if not live["future"].done():
                        live["future"].cancel()
            self.active.pop(session["session_id"], None)
            self.spawn(self.message_queue.finished(session, status))

    async def request(
        self,
        native_session,
        kind,
        method,
        native_id,
        *,
        choices=None,
        response_schema=None,
        native=None,
    ):
        session = next(
            (
                s
                for s in self.sessions.values()
                if s["native_session_id"] == native_session
            ),
            None,
        )
        if session is None:
            raise ConversationError("conflict", "Request names an unknown session")
        turn = self.active.get(session["session_id"], {})
        self.message_queue.pause(
            session,
            "Agent requested approval or input. Resume the queue explicitly after answering.",
        )
        request_id = uuid.uuid4().hex
        record = dict(
            schema="conversation-manager/request-1",
            request_id=request_id,
            native_request_id=native_id,
            native_method=method,
            transport_epoch=self.epoch,
            connection_id=self.ref["connection_id"],
            session_id=session["session_id"],
            native_session_id=native_session,
            turn_id=turn.get("turn_id"),
            native_turn_id=turn.get("native_turn_id"),
            kind=kind,
            choices=choices or [],
            response_schema=response_schema,
            state="pending",
            native=redact(native, self.secrets),
        )
        future = asyncio.get_running_loop().create_future()
        self.requests[request_id] = {
            "record": record,
            "future": future,
            "ack": asyncio.Event(),
        }
        self.store.put("request", request_id, record)
        self.emit("request.pending", record, native_session, native=native)
        return await future

    async def respond(self, params):
        operation_id, request_id = params["operation_id"], params["request_id"]
        live = self.requests.get(request_id)
        if (
            not live
            or live["record"]["state"] != "pending"
            or live["record"]["transport_epoch"] != self.epoch
            or live["future"].done()
        ):
            raise ConversationError(
                "conflict",
                "Request is stale, reserved, resolved or belongs to another epoch",
            )
        record, response = live["record"], params["response"]
        if record["kind"] == "permission" and record["response_schema"] is None:
            if response == {"outcome": "cancelled"}:
                pass
            elif (
                set(response) == {"outcome", "option_id"}
                and response["outcome"] == "selected"
                and response["option_id"]
                in {o.get("optionId") for o in record["choices"]}
            ):
                pass
            else:
                raise ConversationError(
                    "validation",
                    "Permission response must preserve an offered native option ID or cancel",
                )
        else:
            if set(response) != {"native_result"}:
                raise ConversationError(
                    "validation", "Native/user-input response requires native_result"
                )
            value = response["native_result"]
            required = (record["response_schema"] or {}).get("required", [])
            if not isinstance(value, dict) or any(k not in value for k in required):
                raise ConversationError(
                    "validation", "Response does not satisfy advertised schema"
                )
            if (
                record["kind"] == "user_input"
                and record["native_method"] == "_fixture/user_input"
                and not isinstance(value.get("answer"), str)
            ):
                raise ConversationError("validation", "User answer must be a string")
            if hasattr(self.adapter, "validate_response"):
                self.adapter.validate_response(record, value)
            elif record["kind"] == "permission" and value.get("decision") not in (
                "accept",
                "acceptForSession",
                "decline",
                "cancel",
            ):
                raise ConversationError(
                    "validation", "Invalid native approval decision"
                )
            if (
                record["kind"] == "user_input"
                and "answers" in required
                and not isinstance(value.get("answers"), dict)
            ):
                raise ConversationError(
                    "validation", "Native answers must be an object"
                )
        # No await between validation and durable reservation: at most one answer can dispatch.
        record["state"] = "reserved"
        live["operation_id"] = operation_id
        self.store.put("request", request_id, record)
        live["future"].set_result(response)
        if getattr(self.adapter, "reply_mode", None) == "sdk_callback":
            return self.store.finish(
                receipt(
                    operation_id,
                    "accepted",
                    result={
                        "request_id": request_id,
                        "state": "reserved",
                        "delivery": "sdk_callback",
                    },
                    recovery={
                        "connection_ref": self.ref,
                        "request_id": request_id,
                        "transport_epoch": self.epoch,
                    },
                )
            )
        try:
            async with asyncio.timeout(5):
                await live["ack"].wait()
        except TimeoutError:
            return self.finish_error(
                operation_id,
                ConversationError(
                    "uncertain",
                    "Request reply delivery unconfirmed; answer remains reserved",
                    uncertain=True,
                    recovery={"request_id": request_id, "transport_epoch": self.epoch},
                ),
                {"request_id": request_id, "state": record["state"]},
            )
        return self.store.operation(operation_id)

    def response_submitted(self, native_id):
        for live in self.requests.values():
            record = live["record"]
            if (
                record["native_request_id"] == native_id
                and record["state"] == "reserved"
            ):
                record["state"] = "submitted"
                self.store.put("request", record["request_id"], record)
                self.emit(
                    "request.resolved",
                    {
                        "request_id": record["request_id"],
                        "state": "submitted",
                        "operation_id": live["operation_id"],
                    },
                    record["native_session_id"],
                )
                return

    def response_observed(self, native_id, error=None):
        for request_id, live in self.requests.items():
            record = live["record"]
            if record["native_request_id"] == native_id and record["state"] in (
                "reserved",
                "submitted",
            ):
                record["state"] = "resolved" if error is None else "failed"
                self.store.put("request", request_id, record)
                operation_id = live["operation_id"]
                self.store.finish(
                    receipt(
                        operation_id,
                        "completed" if error is None else "failed",
                        result={"request_id": request_id, "state": record["state"]},
                        error=redact(error, self.secrets),
                    )
                )
                self.emit(
                    "request.resolved",
                    {
                        "request_id": request_id,
                        "state": record["state"],
                        "operation_id": operation_id,
                    },
                    record["native_session_id"],
                )
                live["ack"].set()
                self.requests.pop(request_id, None)
                return

    def transport_lost(self, reason):
        if self.lost:
            return
        self.lost = True
        operations = sorted(
            self.inflight
            | {t["operation_id"] for t in self.active.values() if t["operation_id"]}
        )
        for live in self.requests.values():
            record = live["record"]
            if record["state"] in ("pending", "reserved", "submitted"):
                record["state"] = (
                    "uncertain"
                    if record["state"] in ("reserved", "submitted")
                    else "stale"
                )
                self.store.put("request", record["request_id"], record)
                if live.get("operation_id"):
                    self.finish_error(
                        live["operation_id"],
                        ConversationError(
                            "uncertain", "Response delivery lost", uncertain=True
                        ),
                    )
                    live["ack"].set()
        self.emit(
            "connection.closed",
            {
                "reason": reason,
                "affected_operation_ids": operations,
                "recovery": {"connection_ref": self.ref},
            },
        )
        self.store.put(
            "connection",
            self.ref["connection_id"],
            {"connection_ref": self.ref, "state": "closed", "epoch": self.epoch},
        )

    async def dispatch(self, method, params):
        if method == "assess_resources":
            from .resource_finalization import assess

            return await assess(self)
        if method in (
            "finalize_resources",
            "create_session",
            "load_session",
            "restore_session",
        ):
            async with self.resource_lock:
                return await self.mutate(method, params)
        if method == "hello":
            return {
                "connection_ref": self.ref,
                "transport_epoch": self.epoch,
                "backend_connected": not self.lost,
            }
        if method in ("begin_drain", "prepare_stop", "drain_status"):
            blockers = (
                [{"kind": "active_turns", "count": len(self.active)}]
                if self.active
                else []
            )
            pending = sum(
                live["record"]["state"] in ("pending", "reserved", "submitted")
                for live in self.requests.values()
            )
            if pending:
                blockers.append({"kind": "requests", "count": pending})
            if self.inflight:
                blockers.append(
                    {"kind": "inflight_operations", "count": len(self.inflight)}
                )
            if method == "drain_status":
                return {
                    "draining": self.draining,
                    "blockers": blockers,
                    "transport_epoch": self.epoch,
                }
            operation_id = params["operation_id"]
            prior, fresh = self.store.claim(
                operation_id,
                {"connection_id": self.ref["connection_id"], "method": method},
            )
            if not fresh:
                return prior
            if method == "prepare_stop" and (not self.draining or blockers):
                return self.finish_error(
                    operation_id,
                    ConversationError(
                        "busy",
                        "Drain the worker and resolve its blockers before stopping",
                    ),
                )
            self.store.put(
                "worker-drain",
                self.ref["connection_id"],
                {"epoch": self.epoch, "sealed": method == "prepare_stop"},
            )
            self.draining = True
            return self.store.finish(
                receipt(
                    operation_id,
                    "completed",
                    result={
                        "draining": True,
                        "blockers": blockers,
                        "transport_epoch": self.epoch,
                    },
                )
            )
        if method == "capabilities":
            result = self.capabilities()
            services = result["client_services"]
            if hasattr(services, "model_dump"):
                result["client_services"] = services.model_dump(
                    mode="json", by_alias=True
                )
            return result
        if method == "pending_requests":
            session_id = (
                self.session(params["session_ref"])["session_id"]
                if params.get("session_ref")
                else None
            )
            return [
                live["record"]
                for live in self.requests.values()
                if live["record"]["state"] == "pending"
                and (session_id is None or live["record"]["session_id"] == session_id)
            ]
        if method == "queue_state":
            return self.message_queue.view(self.session(params["session_ref"]))
        if method == "session_state":
            session = self.session(params["session_ref"])
            active = self.active.get(session["session_id"])
            return {
                "queue": self.message_queue.view(session),
                "session_ref": session,
                "active_turn": {key: active[key] for key in ("turn_id", "operation_id")}
                if active
                else None,
                "draining": self.draining,
                "closed": self.lost,
                "observed_sequence": self.store.head(self.ref),
                "requests": [
                    live["record"]
                    for live in self.requests.values()
                    if live["record"]["state"] == "pending"
                    and live["record"]["session_id"] == session["session_id"]
                ],
            }
        if method == "turn_result":
            session = self.session(params["session_ref"])
            return self.store.turn_result(session, params.get("turn_id"))
        if method == "events":
            session_id = (
                self.session(params["session_ref"])["session_id"]
                if params.get("session_ref")
                else None
            )
            page = self.store.page(
                self.ref, self.epoch, params.get("after_sequence", 0), session_id
            )
            if not page["events"] and not self.lost and params.get("wait"):
                self.changed.clear()
                try:
                    async with asyncio.timeout(1):
                        await self.changed.wait()
                except TimeoutError:
                    pass
                page = self.store.page(self.ref, self.epoch, page["cursor"], session_id)
            return {**page, "closed": self.lost}
        if method == "history":
            self.supported("history")
            return redact(
                await self.adapter.optional(
                    method, self.session(params["session_ref"]), params
                ),
                self.secrets,
            )
        if method == "history_page":
            return self.store.history_page(
                self.session(params["session_ref"]),
                params.get("cursor", 0),
                params.get("limit", 100),
            )
        if method == "configuration":
            session = self.session(params["session_ref"])
            value = (
                self.adapter.configuration(session)
                if hasattr(self.adapter, "configuration")
                else {"options": []}
            )
            return redact(
                await value if inspect.isawaitable(value) else value, self.secrets
            )
        if method == "commands":
            session = self.session(params["session_ref"])
            value = (
                self.adapter.commands(session)
                if hasattr(self.adapter, "commands")
                else {"state": "unsupported", "commands": []}
            )
            return redact(
                await value if inspect.isawaitable(value) else value, self.secrets
            )
        if method.startswith("mcp."):
            from .mcp import worker_mcp

            return await worker_mcp(self, method[4:], params)
        if method == "shutdown":
            self.shutdown.set()
            return {"termination_requested": True}
        if method not in self.operation_names and method != "respond":
            raise ConversationError("unsupported", "Unknown worker operation")
        return await self.mutate(method, params)
