"""Worker-owned, durable FIFO delivery. Never replay an uncertain dispatch."""

from copy import deepcopy
from hashlib import sha256
from .models import ConversationError
from .store import now


def inspect_delivery(store, session, queue_id):
    """Read retained evidence even when the worker is unavailable. Never dispatch."""
    value = store.get("message-queue", session["session_id"]) or {"items": []}
    item = next((row for row in value["items"] if row["queue_id"] == queue_id), None)
    if item is None:
        raise ConversationError(
            "not_found", "Queued message not found in this conversation"
        )
    result = dict(
        schema="conversation-manager/queued-delivery-1",
        source="retained-evidence",
        item=deepcopy(item),
        receipt=None,
        turn_result=None,
        can_reconcile=False,
        outcome="not_dispatched"
        if item["status"] in ("queued", "removed")
        else "unconfirmed",
    )
    operation = item.get("delivery_operation_id")
    if not operation:
        return result
    try:
        row = store.operation(operation)
        intent = store.operation_intent(operation)
    except ConversationError as error:
        if error.code == "not_found":
            return result
        raise
    steering = item["mode"] == "next_tool_completion"
    params = (
        {
            "session_ref": session,
            "text": item["text"],
            "expected_turn_id": item["target_turn_id"],
            "queue_id": item["queue_id"],
            "delivery_mode": item["mode"],
        }
        if steering
        else {
            "session_ref": session,
            "content": [{"type": "text", "text": item["text"]}],
        }
    )
    expected = {
        "connection_id": session["connection_id"],
        "method": "steer" if steering else "send",
        "params": params,
    }
    if intent != expected:
        result["outcome"] = "intent_conflict"
        return result
    result["receipt"] = row
    turn_id = (row.get("result") or {}).get("turn_id")
    if turn_id:
        try:
            result["turn_result"] = store.turn_result(session, turn_id)
        except ConversationError as error:
            if error.code != "not_found":
                raise
    handed_off = row["status"] in ("accepted", "completed") and bool(turn_id)
    terminal = result["turn_result"]
    known_failure = row["status"] in ("failed", "unsupported") and not (
        row.get("error") or {}
    ).get("uncertain")
    if handed_off or (terminal and not steering):
        result["outcome"] = (
            "handed_off" if steering else terminal["status"] if terminal else "accepted"
        )
        result["can_reconcile"] = item["status"] in (
            "dispatching",
            "uncertain",
            "failed",
        )
    elif known_failure:
        result["outcome"] = "failed"
        result["can_reconcile"] = item["status"] in ("dispatching", "uncertain")
    return result


class MessageQueue:
    def __init__(self, core):
        self.core = core
        self.running = {}

    def get(self, session):
        return self.core.store.get("message-queue", session["session_id"]) or {
            "paused": False,
            "reason": None,
            "items": [],
        }

    def save(self, session, value):
        terminal = [
            row for row in value["items"] if row["status"] in ("delivered", "removed")
        ]
        retained = {row["queue_id"] for row in terminal[-20:]}
        value["items"] = [
            row
            for row in value["items"]
            if row["status"] not in ("delivered", "removed")
            or row["queue_id"] in retained
        ]
        self.core.store.put("message-queue", session["session_id"], value)
        self.core.emit(
            "queue.updated",
            {
                "paused": value["paused"],
                "reason": value["reason"],
                "items": [
                    {
                        key: row.get(key)
                        for key in ("queue_id", "status", "mode", "turn_id")
                    }
                    for row in value["items"]
                ],
            },
            session["native_session_id"],
        )

    def modes(self):
        steering = bool(self.core.adapter.operations.get("steer", {}).get("supported"))
        return {
            "after_turn": {
                "supported": bool(
                    self.core.adapter.operations.get("send", {}).get("supported")
                )
            },
            "next_tool_completion": {
                "supported": steering,
                "requires_running_tools": True,
                "timing": "observed-completion",
                "reason": None
                if steering
                else "This adapter cannot steer an active turn.",
            },
            "after_tool": {
                "supported": False,
                "reason": "Exact tool-boundary delivery is unsupported; use next_tool_completion for observed completion.",
            },
        }

    def view(self, session):
        active = self.core.active.get(session["session_id"])
        tools = (
            self.running.get((session["session_id"], active["turn_id"]), set())
            if active
            else set()
        )
        return deepcopy(
            {
                "schema": "conversation-manager/message-queue-1",
                **self.get(session),
                "modes": self.modes(),
                "running_tools": sorted(tools),
            }
        )

    def observe(self, session, turn, kind, payload, native=None):
        if not session or not turn or not kind.startswith("tool."):
            return
        current = self.core.active.get(session["session_id"])
        if not current or current["turn_id"] != turn["turn_id"]:
            return
        if (
            isinstance(native, dict)
            and native.get("turnId")
            and current.get("native_turn_id")
            and native["turnId"] != current["native_turn_id"]
        ):
            return
        tool = payload.get("tool_call_id")
        if not isinstance(tool, str) or not tool:
            return
        key = (session["session_id"], turn["turn_id"])
        running = self.running.setdefault(key, set())
        status = str(payload.get("status", "")).lower().replace("_", "")
        terminal = kind == "tool.result" or status in (
            "completed",
            "failed",
            "error",
            "cancelled",
            "canceled",
            "interrupted",
            "declined",
            "denied",
            "success",
            "succeeded",
        )
        if terminal:
            running.discard(tool)
            value, changed = self.get(session), False
            for item in value["items"]:
                if (
                    item["status"] == "queued"
                    and item["mode"] == "next_tool_completion"
                    and item["target_turn_id"] == turn["turn_id"]
                    and tool in item["remaining_tools"]
                ):
                    item["remaining_tools"].remove(tool)
                    changed = True
            if changed:
                self.save(session, value)
                self.core.spawn(self.deliver(session))
        elif kind == "tool.start" or status in (
            "pending",
            "running",
            "inprogress",
            "executing",
        ):
            running.add(tool)

    def pending_requests(self, session):
        return any(
            live["record"]["session_id"] == session["session_id"]
            and live["record"]["state"]
            in ("pending", "reserved", "submitted", "uncertain")
            for live in self.core.requests.values()
        )

    def pause(self, session, reason):
        value = self.get(session)
        value.update(paused=True, reason=reason)
        self.save(session, value)

    @staticmethod
    def text(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 32000:
            raise ConversationError(
                "validation", "Queue messages require 1–32000 characters"
            )
        return value

    def mutate(self, session, params):
        action = params["action"]
        value = self.get(session)
        items = value["items"]
        if action == "enqueue":
            mode = params.get("mode", "after_turn")
            if mode not in ("after_turn", "next_tool_completion"):
                raise ConversationError(
                    "unsupported", "Choose after_turn or next_tool_completion"
                )
            self.core.supported("steer" if mode == "next_tool_completion" else "send")
            active = self.core.active.get(session["session_id"])
            if (
                not active
                or active["turn_id"] != params.get("expected_turn_id")
                or active["cancel_operations"]
            ):
                raise ConversationError(
                    "conflict", "The targeted turn is no longer running"
                )
            if (
                sum(item["status"] not in ("delivered", "removed") for item in items)
                >= 100
            ):
                raise ConversationError(
                    "validation", "At most 100 pending messages per conversation"
                )
            text = self.text(params.get("text"))
            if (
                sum(
                    len(row["text"])
                    for row in items
                    if row["status"] not in ("removed", "delivered")
                )
                + len(text)
                > 128000
            ):
                raise ConversationError(
                    "validation", "Queued text exceeds the conversation queue limit"
                )
            tools = sorted(
                self.running.get((session["session_id"], active["turn_id"]), set())
            )
            if mode == "next_tool_completion" and not tools:
                raise ConversationError(
                    "conflict",
                    "No currently running tools; choose after current turn or wait for a tool to start",
                )
            items.append(
                dict(
                    queue_id=params["operation_id"],
                    text=text,
                    mode=mode,
                    revision=1,
                    target_turn_id=active["turn_id"],
                    status="queued",
                    created_at=now(),
                    **(
                        {"captured_tools": tools, "remaining_tools": list(tools)}
                        if mode == "next_tool_completion"
                        else {}
                    ),
                )
            )
            if self.pending_requests(session):
                value.update(
                    paused=True,
                    reason="Agent is waiting for an approval or answer; resume the queue explicitly.",
                )
        elif action in ("edit", "remove"):
            item = next(
                (row for row in items if row["queue_id"] == params.get("queue_id")),
                None,
            )
            if not item or item["status"] != "queued":
                raise ConversationError(
                    "conflict", "Only an undelivered queued message can be changed"
                )
            if "expected_revision" in params:
                expected = params["expected_revision"]
                if type(expected) is not int or expected != item.get("revision", 1):
                    raise ConversationError(
                        "conflict", "Queued message changed; refresh before editing"
                    )
            if action == "edit":
                text = self.text(params.get("text"))
                if (
                    sum(
                        len(row["text"])
                        for row in items
                        if row["status"] not in ("removed", "delivered")
                        and row is not item
                    )
                    + len(text)
                    > 128000
                ):
                    raise ConversationError(
                        "validation", "Queued text exceeds the conversation queue limit"
                    )
                item["text"] = text
            else:
                item["status"] = "removed"
            item["revision"] = item.get("revision", 1) + 1
        elif action == "reconcile":
            evidence = inspect_delivery(
                self.core.store, session, params.get("queue_id")
            )
            if not evidence["can_reconcile"]:
                raise ConversationError(
                    "uncertain",
                    "No confirmed matching delivery outcome; do not replay this message",
                    uncertain=True,
                )
            item = next(row for row in items if row["queue_id"] == params["queue_id"])
            observed = evidence["receipt"]
            item["receipt"] = observed
            turn_id = (observed.get("result") or {}).get("turn_id")
            if evidence["outcome"] != "failed" or (
                evidence["turn_result"] and item["mode"] == "after_turn"
            ):
                item.update(status="delivered", turn_id=turn_id)
                # Restore missing input history without creating another provider turn.
                cursor, found = 0, False
                while True:
                    page = self.core.store.history_page(
                        session, cursor=cursor, limit=500
                    )
                    if any(
                        e["kind"] == "message.input"
                        and e["payload"].get("queue_id") == item["queue_id"]
                        for e in page["entries"]
                    ):
                        found = True
                        break
                    if not page["has_more"]:
                        break
                    cursor = page["cursor"]
                if not found:
                    self.core.emit(
                        "message.input",
                        {
                            "text": item["text"],
                            "input_id": item["delivery_operation_id"],
                            "delivery": "steer"
                            if item["mode"] == "next_tool_completion"
                            else "after_turn",
                            "delivery_mode": item["mode"],
                            "queue_id": item["queue_id"],
                        },
                        session["native_session_id"],
                        turn_ref={"turn_id": turn_id, "native_turn_id": None},
                    )
            else:
                item["status"] = "failed"
            value.update(
                paused=True,
                reason="Delivery reconciled from retained evidence. Resume remaining messages explicitly.",
            )
        elif action == "pause":
            value.update(paused=True, reason="Queue paused by user.")
        elif action == "resume":
            if self.pending_requests(session):
                raise ConversationError(
                    "busy",
                    "Resolve pending approvals/questions before resuming the queue",
                )
            active = self.core.active.get(session["session_id"])
            if any(
                row["status"] == "queued"
                and row["mode"] == "next_tool_completion"
                and (not active or row["target_turn_id"] != active["turn_id"])
                for row in items
            ):
                raise ConversationError(
                    "conflict",
                    "The guidance target turn ended. Remove the retained message or send it explicitly in another turn.",
                )
            if any(row["status"] == "dispatching" for row in items):
                raise ConversationError(
                    "uncertain",
                    "Inspect unconfirmed delivery before resuming; it will not be replayed",
                    uncertain=True,
                )
            value.update(paused=False, reason=None)
        else:
            raise ConversationError("validation", "Unknown queue action")
        self.save(session, value)
        return self.view(session)

    async def finished(self, session, status):
        active = self.core.active.get(session["session_id"])
        for key in list(self.running):
            if key[0] == session["session_id"] and (
                not active or key[1] != active["turn_id"]
            ):
                self.running.pop(key, None)
        value = self.get(session)
        expired = False
        for item in value["items"]:
            if (
                item["status"] == "queued"
                and item["mode"] == "next_tool_completion"
                and (not active or item["target_turn_id"] != active["turn_id"])
            ):
                item["blocked_reason"] = (
                    "Target turn ended before guidance was delivered."
                )
                expired = True
        if expired:
            value.update(
                paused=True,
                reason="Target turn ended. Retained tool guidance will not be sent as a new turn.",
            )
            self.save(session, value)
        if status != "completed":
            self.pause(
                session,
                "Previous turn "
                + (status or "has an unconfirmed outcome")
                + ". Resume explicitly after inspection.",
            )
            return
        await self.deliver(session)

    async def deliver(self, session):
        if session["session_id"] in getattr(self.core, "configuring", set()):
            return
        core = self.core
        if core.lost or core.draining:
            return
        value = self.get(session)
        if (
            value["paused"]
            or self.pending_requests(session)
            or any(row["status"] == "dispatching" for row in value["items"])
        ):
            return
        active = core.active.get(session["session_id"])
        if not active and any(
            row["status"] == "queued" and row["mode"] == "next_tool_completion"
            for row in value["items"]
        ):
            value.update(
                paused=True,
                reason="Target turn ended. Retained tool guidance will not be sent as a new turn.",
            )
            for row in value["items"]:
                if row["status"] == "queued" and row["mode"] == "next_tool_completion":
                    row["blocked_reason"] = (
                        "Target turn ended before guidance was delivered."
                    )
            self.save(session, value)
            return
        mode = "next_tool_completion" if active else "after_turn"
        item = next(
            (
                row
                for row in value["items"]
                if row["status"] == "queued" and row["mode"] == mode
            ),
            None,
        )
        if item is None:
            return
        if active and (
            active["cancel_operations"]
            or item["target_turn_id"] != active["turn_id"]
            or item["remaining_tools"]
        ):
            return
        item["status"] = "dispatching"
        item["delivery_operation_id"] = (
            "queue-steer:" if active else "queue-send:"
        ) + sha256(item["queue_id"].encode()).hexdigest()
        self.save(session, value)
        params = dict(session_ref=session, operation_id=item["delivery_operation_id"])
        if active:
            params.update(
                text=item["text"],
                expected_turn_id=item["target_turn_id"],
                queue_id=item["queue_id"],
                delivery_mode=mode,
            )
        else:
            params["content"] = item["text"]
        result = await core.mutate("steer" if active else "send", params)
        value = self.get(session)
        row = next(row for row in value["items"] if row["queue_id"] == item["queue_id"])
        row["receipt"] = result
        if result["status"] in ("accepted", "completed"):
            row.update(status="delivered", turn_id=result["result"]["turn_id"])
            if not active:  # steering publishes input and receipt atomically in Core
                core.emit(
                    "message.input",
                    {
                        "text": item["text"],
                        "input_id": item["delivery_operation_id"],
                        "delivery": "after_turn",
                        "queue_id": item["queue_id"],
                    },
                    session["native_session_id"],
                    turn_ref=core.active.get(session["session_id"]),
                )
        else:
            row["status"] = "uncertain" if result["status"] == "uncertain" else "failed"
            value.update(
                paused=True,
                reason="Queued delivery "
                + row["status"]
                + "; inspect before continuing.",
            )
        self.save(session, value)
        if active and result["status"] == "completed":
            core.spawn(self.deliver(session))
