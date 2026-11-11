"""Verified Codex 0.162.1 session controls, shared with the compatibility client.

Settings mutations use thread/settings/update and wait for its authoritative
thread/settings/updated notification. They never write user configuration.
"""

import asyncio
import re

from .models import ConversationError
from .native_command_catalog import inventory, TURN_COMMANDS, DIRECT_COMMANDS
from .slash_commands import SETTING_COMMANDS, setting_selection


class NativeControls:
    def __init__(self, rpc, initialization=None):
        self.rpc = rpc
        self.initialization = initialization or {}
        self.settings, self.waiters = {}, {}
        self.models, self.permission_profiles = None, {}

    @property
    def supported(self):
        info = self.initialization
        match = re.search(
            r"(\d+)\.(\d+)\.(\d+)",
            str(
                (info.get("serverInfo") or {}).get("version")
                or info.get("userAgent")
                or ""
            ),
        )
        return bool(match and tuple(map(int, match.groups())) >= (0, 162, 1))

    def capture(self, session_id, settings):
        current = self.settings.setdefault(session_id, {})
        for key in ("model", "serviceTier", "effort"):
            source = (
                "reasoningEffort"
                if key == "effort" and "reasoningEffort" in settings
                else key
            )
            if source in settings:
                current[key] = settings[source]
        for key in (
            "approvalPolicy",
            "approvalsReviewer",
            "sandboxPolicy",
            "activePermissionProfile",
            "cwd",
            "collaborationMode",
        ):
            if key in settings:
                current[key] = settings[key]
        if isinstance(settings.get("activePermissionProfile"), dict):
            current["permissions"] = settings["activePermissionProfile"]["id"]
        elif "activePermissionProfile" in settings:
            current.pop("permissions", None)
        if isinstance(settings.get("collaborationMode"), dict):
            current["mode"] = settings["collaborationMode"]["mode"]
        waiter = self.waiters.get(session_id)
        if (
            waiter
            and not waiter["future"].done()
            and all(
                key in current and current[key] == value
                for key, value in waiter["expected"].items()
            )
        ):
            waiter["future"].set_result(dict(current))

    async def catalog(self):
        if self.models is None:
            models, cursor, seen = [], None, set()
            for _ in range(20):
                page = await self.rpc(
                    "model/list",
                    {
                        "limit": 100,
                        "includeHidden": False,
                        **({"cursor": cursor} if cursor else {}),
                    },
                )
                models.extend(page.get("data", []))
                cursor = page.get("nextCursor")
                if not cursor:
                    self.models = models
                    break
                if cursor in seen:
                    raise ConversationError(
                        "validation", "Model catalog pagination repeated"
                    )
                seen.add(cursor)
            if self.models is None:
                raise ConversationError(
                    "validation", "Model catalog pagination did not finish"
                )
        return self.models

    async def configuration(self, session_id):
        if not self.supported:
            return {"options": []}
        models = await self.catalog()
        settings = self.settings.get(session_id, {})
        model = settings.get("model")
        choices = [
            {
                "value": row.get("model", row["id"]),
                "name": row.get("displayName", row["id"]),
            }
            for row in models
        ]
        if model and not any(row["value"] == model for row in choices):
            choices.append({"value": model, "name": model})
        options = [
            {
                "id": "model",
                "name": "Model",
                "category": "model",
                "type": "select",
                "currentValue": model or "",
                "options": choices,
            }
        ]
        selected = next(
            (row for row in models if row.get("model", row["id"]) == model), {}
        )
        efforts = selected.get("supportedReasoningEfforts") or []
        if efforts:
            options.append(
                {
                    "id": "effort",
                    "name": "Reasoning",
                    "category": "thought_level",
                    "type": "select",
                    "currentValue": settings.get("effort")
                    or selected.get("defaultReasoningEffort", ""),
                    "options": [
                        {
                            "value": row["reasoningEffort"],
                            "name": row["reasoningEffort"].capitalize(),
                        }
                        for row in efforts
                    ],
                }
            )
        tiers = selected.get("serviceTiers") or [
            {"id": tier, "name": tier.capitalize()}
            for tier in selected.get("additionalSpeedTiers", [])
        ]
        tier_choices = [{"value": "default", "name": "Standard"}]
        tier_choices.extend(
            {"value": row["id"], "name": row.get("name", row["id"])}
            for row in tiers
            if row["id"] != "default"
        )
        current = settings.get("serviceTier") or "default"
        if len(tier_choices) > 1:
            options.append(
                {
                    "id": "serviceTier",
                    "name": "Speed",
                    "category": "model_config",
                    "type": "select",
                    "currentValue": current,
                    "options": tier_choices,
                }
            )
        if settings.get("mode") in ("plan", "default"):
            options.append(
                dict(
                    id="mode",
                    name="Agent mode",
                    category="mode",
                    type="select",
                    currentValue=settings["mode"],
                    options=[
                        dict(value="default", name="Default"),
                        dict(value="plan", name="Plan"),
                    ],
                )
            )
        if settings.get("cwd"):
            cwd = settings["cwd"]
            if cwd not in self.permission_profiles:
                self.permission_profiles[cwd] = await self.pages(
                    "permissionProfile/list", {"cwd": cwd}
                )
                return await self.configuration(session_id)
            choices = [
                dict(value=row["id"], name=row["id"])
                for row in self.permission_profiles[cwd]
                if row.get("allowed")
            ]
            if choices:
                options.append(
                    dict(
                        id="permissions",
                        name="Permissions",
                        category="permissions",
                        type="select",
                        currentValue=settings.get("permissions") or "Current policy",
                        options=choices,
                    )
                )
        return {"options": options, "nativeSettings": dict(settings)}

    async def pages(self, method, params):
        rows, cursor, seen = [], None, set()
        for _ in range(20):
            result = await self.rpc(
                method, {**params, **({"cursor": cursor} if cursor else {})}
            )
            rows.extend(result.get("data", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return rows
            if cursor in seen:
                break
            seen.add(cursor)
        raise ConversationError(
            "validation", "Provider catalog pagination did not finish"
        )

    async def set_config(self, session_id, config_id, value):
        configuration = await self.configuration(session_id)
        option = next(
            (row for row in configuration["options"] if row["id"] == config_id), None
        )
        if not option or not any(row["value"] == value for row in option["options"]):
            raise ConversationError(
                "unsupported", "Choose an advertised session setting"
            )
        if session_id in self.waiters:
            raise ConversationError("busy", "Another settings operation is pending")
        future = asyncio.get_running_loop().create_future()
        target = None if config_id == "serviceTier" and value == "default" else value
        self.waiters[session_id] = {"future": future, "expected": {config_id: target}}
        try:
            wire_key, wire_value = config_id, target
            if config_id == "mode":
                settings = self.settings[session_id]
                wire_key, wire_value = (
                    "collaborationMode",
                    {
                        "mode": value,
                        "settings": {
                            "model": settings["model"],
                            "reasoning_effort": settings.get("effort"),
                            "developer_instructions": None,
                        },
                    },
                )
            params = {"threadId": session_id, wire_key: wire_value}
            if config_id == "model":
                selected = next(
                    (
                        row
                        for row in self.models or []
                        if row.get("model", row["id"]) == value
                    ),
                    None,
                )
                settings = self.settings.get(session_id, {})
                if selected is not None:
                    tiers = [
                        row["id"] for row in selected.get("serviceTiers") or []
                    ] + (selected.get("additionalSpeedTiers") or [])
                    if (
                        settings.get("serviceTier")
                        and settings["serviceTier"] not in tiers
                    ):
                        params["serviceTier"] = None
                    efforts = [
                        row["reasoningEffort"]
                        for row in selected.get("supportedReasoningEfforts") or []
                    ]
                    default_effort = selected.get("defaultReasoningEffort")
                    if (
                        settings.get("effort")
                        and settings["effort"] not in efforts
                        and default_effort in efforts
                    ):
                        params["effort"] = default_effort
            current_mode = self.settings.get(session_id, {}).get("collaborationMode")
            if (
                config_id in ("model", "effort")
                and isinstance(current_mode, dict)
                and isinstance(current_mode.get("settings"), dict)
            ):
                mode_settings = {
                    **current_mode["settings"],
                    "model" if config_id == "model" else "reasoning_effort": value,
                }
                if "effort" in params:
                    mode_settings["reasoning_effort"] = params["effort"]
                params["collaborationMode"] = {
                    **current_mode,
                    "settings": mode_settings,
                }
            self.waiters[session_id]["expected"].update(
                {
                    key: params[key]
                    for key in ("model", "effort", "serviceTier")
                    if key in params
                }
            )
            await self.rpc("thread/settings/update", params)
            try:
                await asyncio.wait_for(asyncio.shield(future), 10)
            except TimeoutError as exc:
                raise ConversationError(
                    "uncertain",
                    "Settings update was acknowledged without confirmed settings; inspect before retrying",
                    uncertain=True,
                ) from exc
            return await self.configuration(session_id)
        finally:
            self.waiters.pop(session_id, None)
            if not future.done():
                future.cancel()

    async def commands(self, session_id):
        rows = inventory()
        rows.extend(
            dict(
                name=name,
                description=description,
                execution="unsupported",
                requiresIdle=True,
                reason="The selected model does not advertise this setting",
            )
            for name, description in [
                ("reasoning", "Choose reasoning effort"),
                ("effort", "Choose reasoning effort"),
                ("mode", "Choose conversation mode"),
                ("speed", "Choose native service tier"),
            ]
        )
        hints = {
            "rename": "conversation name",
            "review": "optional review instructions",
            "mcp": "optional: verbose",
            "goal": "objective | pause | resume | clear",
            "skills": "optional: skill-name [instructions]",
        }
        for row in rows:
            if row["name"] in hints:
                row["inputHint"] = hints[row["name"]]
            if row["name"] == "setup-default-sandbox":
                row.update(
                    execution="unsupported",
                    reason="This command is specific to the Windows terminal.",
                )
                row.pop("terminal", None)
        config = await self.configuration(session_id)
        if not self.supported:
            for row in rows:
                if row["name"] in DIRECT_COMMANDS:
                    row.update(
                        execution="unsupported",
                        reason="This mapping requires verified Codex 0.162.1 or newer.",
                        terminal=True,
                    )
        options = {row["id"]: row for row in config["options"]}
        for name, config_id in (
            ("model", "model"),
            ("permissions", "permissions"),
            ("plan", "mode"),
            ("mode", "mode"),
            ("reasoning", "effort"),
            ("effort", "effort"),
            ("speed", "serviceTier"),
        ):
            if config_id in options:
                next(row for row in rows if row["name"] == name).update(
                    execution="setting",
                    configId=config_id,
                    requiresIdle=True,
                    options=options[config_id]["options"],
                    inputHint=" | ".join(
                        str(choice["value"]) for choice in options[config_id]["options"]
                    ),
                )
        speed = next(
            (row for row in config["options"] if row["id"] == "serviceTier"), None
        )
        if speed and any(
            row["value"] in ("fast", "priority") for row in speed["options"]
        ):
            next(row for row in rows if row["name"] == "fast").update(
                execution="setting",
                configId="serviceTier",
                inputHint="on | off | status",
                requiresIdle=True,
                options=speed["options"],
            )
        else:
            row = next(row for row in rows if row["name"] == "fast")
            row.update(
                execution="unsupported",
                reason="The selected model does not advertise Fast mode.",
            )
            row.pop("terminal", None)
        return {
            "state": "ready",
            "commands": rows,
            "source": "codex-cli-reference-2026-10-15",
            "runtime": self.initialization.get("userAgent"),
        }

    @staticmethod
    def validate_command(name, arguments):
        if (
            not isinstance(arguments, str)
            or name not in TURN_COMMANDS | DIRECT_COMMANDS | SETTING_COMMANDS.keys()
            or name
            in (
                "compact",
                "init",
                "recap",
                "status",
                "debug-config",
                "usage",
                "ps",
                "stop",
                "apps",
                "diff",
            )
            and arguments
        ):
            raise ConversationError(
                "unsupported", "This native command is not available"
            )
        if name == "rename" and not 1 <= len(arguments) <= 120:
            raise ConversationError(
                "validation", "Provide a conversation name of at most 120 characters"
            )

    async def command(self, session_id, name, arguments, *, extra_inputs=None):
        self.validate_command(name, arguments)
        if name in SETTING_COMMANDS:
            key, value, option = setting_selection(
                name, arguments, await self.configuration(session_id)
            )
            return (
                {"output": option}
                if value is None
                else await self.set_config(session_id, key, value)
            )
        if name in DIRECT_COMMANDS and not self.supported:
            raise ConversationError(
                "unsupported", "This mapping requires verified Codex 0.162.1 or newer"
            )
        if name == "compact" and not arguments:
            return await self.rpc("thread/compact/start", {"threadId": session_id})
        if name == "review":
            return await self.rpc(
                "review/start",
                {
                    "threadId": session_id,
                    "delivery": "inline",
                    "target": {"type": "custom", "instructions": arguments}
                    if arguments
                    else {"type": "uncommittedChanges"},
                },
            )
        if name in ("init", "recap"):
            text = (
                "Inspect this project and create an AGENTS.md with accurate build, test, and contribution instructions. Preserve existing instructions."
                if name == "init"
                else "Summarize this conversation, its decisions, completed work, and remaining work. Do not compact or change files."
            )
            return await self.rpc(
                "turn/start",
                {
                    "threadId": session_id,
                    "input": [{"type": "text", "text": text}, *(extra_inputs or [])],
                },
            )
        if name in ("status", "debug-config"):
            settings = self.settings.get(session_id, {})
            configuration = await self.configuration(session_id)
            return {
                "output": {
                    "settings": {
                        key: settings[key]
                        for key in (
                            "model",
                            "effort",
                            "serviceTier",
                            "approvalPolicy",
                            "approvalsReviewer",
                            "activePermissionProfile",
                            "cwd",
                            "sandboxPolicy",
                            "mode",
                        )
                        if key in settings
                    },
                    "options": configuration["options"],
                }
            }
        if name == "diff":
            result = await self.rpc(
                "thread/read", {"threadId": session_id, "includeTurns": True}
            )
            return {
                "output": [
                    item
                    for turn in result.get("thread", {}).get("turns", [])
                    for item in turn.get("items", [])
                    if item.get("type") == "fileChange"
                ]
            }
        if name == "rename":
            await self.rpc(
                "thread/name/set", {"threadId": session_id, "name": arguments}
            )
            return {"title": arguments, "output": "Conversation renamed."}
        if name == "mcp":
            if arguments not in ("", "verbose"):
                raise ConversationError("validation", "Use /mcp or /mcp verbose")
            return {
                "output": await self.pages(
                    "mcpServerStatus/list", {"threadId": session_id}
                )
            }
        if name == "usage":
            result = await self.rpc("account/rateLimits/read", {})
            return {
                "output": {
                    key: result[key]
                    for key in (
                        "rateLimits",
                        "rateLimitsByLimitId",
                        "ordinaryUsageAllowed",
                    )
                    if key in result
                }
            }
        if name == "ps":
            return {
                "output": await self.pages(
                    "thread/backgroundTerminals/list", {"threadId": session_id}
                )
            }
        if name == "stop":
            await self.rpc("thread/backgroundTerminals/clean", {"threadId": session_id})
            return {"output": "Background terminal cleanup requested."}
        if name == "apps":
            return {"output": await self.pages("app/list", {"threadId": session_id})}
        if name == "skills":
            result = await self.rpc(
                "skills/list",
                {
                    "cwds": [self.settings[session_id]["cwd"]]
                    if self.settings.get(session_id, {}).get("cwd")
                    else [],
                    "forceReload": False,
                },
            )
            if arguments:
                name, _, prompt = arguments.partition(" ")
                skills = [
                    skill
                    for group in result.get("data", [])
                    for skill in group.get("skills", [])
                ]
                selected = [
                    skill
                    for skill in skills
                    if skill["name"] == name and skill.get("enabled", True)
                ]
                if len(selected) != 1:
                    raise ConversationError(
                        "validation",
                        "Choose one enabled skill advertised for this project",
                    )
                return await self.rpc(
                    "turn/start",
                    {
                        "threadId": session_id,
                        "input": [
                            {
                                "type": "skill",
                                "name": name,
                                "path": selected[0]["path"],
                            },
                            {
                                "type": "text",
                                "text": prompt.strip()
                                or "Use the selected skill for the current task.",
                            },
                            *(
                                item
                                for item in extra_inputs or []
                                if item.get("path") != selected[0]["path"]
                            ),
                        ],
                    },
                )
            return {"output": result.get("data", []), "resource": "skills"}
        if name == "goal":
            if not arguments:
                result = await self.rpc("thread/goal/get", {"threadId": session_id})
            elif arguments == "clear":
                result = await self.rpc(
                    "thread/goal/clear", {"threadId": session_id, "origin": "user"}
                )
            else:
                params = {"threadId": session_id, "origin": "user"}
                if arguments in ("pause", "resume"):
                    params["status"] = "paused" if arguments == "pause" else "active"
                else:
                    objective = arguments.removeprefix("edit ").strip()
                    if not 1 <= len(objective) <= 4000:
                        raise ConversationError(
                            "validation", "A goal must contain 1–4,000 characters"
                        )
                    params.update(objective=objective, status="active")
                result = await self.rpc("thread/goal/set", params)
            return {"output": result}
