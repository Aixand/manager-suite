"""Session controls mapped to the installed OpenCode V2 public HTTP contract."""

from copy import deepcopy
import urllib.parse

from .models import ConversationError

PLAN_AGENT = "plan"
PLAN_RULES = [
    {"action": action, "resource": "*", "effect": "deny"}
    for action in ("write", "edit", "apply_patch", "shell", "subagent")
]
PERMISSIONS = {
    "ask": "Ask before tool access",
    "deny": "Deny tool access",
    "allow": "Allow tool access",
    "native": "Native configured defaults",
}


def reference(value):
    return (
        value["providerID"]
        + "/"
        + value["id"]
        + (
            "#" + value["variant"]
            if value.get("variant") not in (None, "default")
            else ""
        )
    )


class OpenCodeControls:
    def __init__(self, adapter):
        self.adapter = adapter

    async def snapshot(self, session):
        a = self.adapter
        info = (await a.http("GET", "/api/session/" + session["native_session_id"]))[
            "data"
        ]
        models = (
            await a.http(
                "GET",
                "/api/model?"
                + urllib.parse.urlencode({"location[directory]": session["cwd"]}),
            )
        )["data"]
        current = info.get("model")
        model = next(
            (
                m
                for m in models
                if current
                and m["id"] == current["id"]
                and m["providerID"] == current["providerID"]
            ),
            None,
        )
        return info, models, current, model

    def saved(self, identity, info):
        value = self.adapter.core.store.get("opencode-controls", identity)
        if value is None:
            value = dict(
                default_agent=info.get("agent") or "build",
                base_rules=deepcopy(info.get("permissions") or []),
                permission_mode="native",
            )
            self.adapter.core.store.put("opencode-controls", identity, value)
        return value

    @staticmethod
    def variants(model):
        base = (model or {}).get("settings", {})
        declared = (model or {}).get("variants", [])
        default = next((v for v in declared if v["id"] == "default"), {})
        return [
            dict(id="default", settings={**base, **default.get("settings", {})})
        ] + [
            {**v, "settings": {**base, **v.get("settings", {})}}
            for v in declared
            if v["id"] != "default"
        ]

    @staticmethod
    def option(identifier, name, current, values, **extra):
        return dict(
            id=identifier,
            category={
                "effort": "thought_level",
                "serviceTier": "model_config",
                "variant": "model_config",
            }.get(identifier, identifier),
            name=name,
            type="select",
            currentValue=current,
            options=[dict(value=value, name=label) for value, label in values],
            **extra,
        )

    async def configuration(self, session):
        info, models, current, model = await self.snapshot(session)
        identity = session["native_session_id"]
        saved = self.saved(identity, info)
        mode = "plan" if info.get("agent") == PLAN_AGENT else "default"
        variants = self.variants(model)
        selected = next(
            (
                v
                for v in variants
                if current and v["id"] == current.get("variant", "default")
            ),
            {},
        )
        fields = selected.get("settings", {})
        options = [
            self.option(
                "model",
                "Model",
                reference(current) if current else "",
                [
                    (m["providerID"] + "/" + m["id"], m.get("name", m["id"]))
                    for m in models
                    if m.get("enabled", True)
                ],
            )
        ]
        if current and not any(
            row["value"] == reference(current) for row in options[0]["options"]
        ):
            options[0]["options"].append(
                dict(value=reference(current), name=reference(current))
            )
        if model:
            options.append(
                self.option(
                    "variant",
                    "Native model variant",
                    (current or {}).get("variant", "default"),
                    [(v["id"], v["id"]) for v in variants],
                )
            )
        for key, field, label in [
            ("effort", "reasoningEffort", "Reasoning effort"),
            ("serviceTier", "serviceTier", "Speed / service tier"),
        ]:
            other = "serviceTier" if key == "effort" else "reasoningEffort"
            choices = []
            for variant in variants:
                settings = variant.get("settings", {})
                value = settings.get(field, "default")
                if (
                    settings.get(other, "default") == fields.get(other, "default")
                    and value not in choices
                ):
                    choices.append(value)
            if any(field in v.get("settings", {}) for v in variants):
                options.append(
                    self.option(
                        key,
                        label,
                        fields.get(field, "default"),
                        [(v, v) for v in choices],
                        mapping="native-model-variant",
                    )
                )
        base = (
            saved["base_rules"]
            if saved["permission_mode"] == "native"
            else self.permission_rules(saved["permission_mode"])
        )
        expected = base + (PLAN_RULES if mode == "plan" else [])
        permission = (
            saved["permission_mode"]
            if info.get("permissions", []) == expected
            else "custom"
        )
        agents = (
            await self.adapter.http(
                "GET",
                "/api/agent?"
                + urllib.parse.urlencode({"location[directory]": session["cwd"]}),
            )
        )["data"]
        modes = [("default", "Default")] + (
            [("plan", "Plan")]
            if any(row.get("id") == PLAN_AGENT for row in agents)
            else []
        )
        options.extend(
            [
                self.option(
                    "mode",
                    "Conversation mode",
                    mode,
                    modes,
                    mapping="native-agent",
                    enforcement="Planning agent with native write/shell denials",
                ),
                self.option(
                    "permissions",
                    "Tool permissions",
                    permission,
                    list(PERMISSIONS.items()),
                    enforcement="Native tool permission rules; not an operating-system sandbox",
                ),
            ]
        )
        return dict(options=options)

    @staticmethod
    def permission_rules(value):
        if value not in ("ask", "deny", "allow"):
            raise ConversationError(
                "validation", "Choose an advertised native tool permission mode"
            )
        return [dict(action="*", resource="*", effect=value)]

    async def set_config(self, session, key, value):
        a, identity = self.adapter, session["native_session_id"]
        configuration = await self.configuration(session)
        option = next(
            (row for row in configuration["options"] if row["id"] == key), None
        )
        if option is None:
            raise ConversationError(
                "unsupported",
                "The selected native model does not advertise this setting",
            )
        if key != "model" and value not in [row["value"] for row in option["options"]]:
            raise ConversationError(
                "validation", "Choose an advertised native setting value"
            )
        info, models, current, model = await self.snapshot(session)
        saved = deepcopy(self.saved(identity, info))
        if key in ("model", "variant", "effort", "serviceTier"):
            target = a.model(value) if key == "model" else deepcopy(current)
            if not target:
                raise ConversationError("unsupported", "Select a native model first")
            selected_model = next(
                (
                    m
                    for m in models
                    if m["id"] == target["id"]
                    and m["providerID"] == target["providerID"]
                    and m.get("enabled", True)
                ),
                None,
            )
            if selected_model is None:
                raise ConversationError(
                    "validation", "Choose a model advertised by this environment"
                )
            variants = self.variants(selected_model)
            if key == "variant":
                target["variant"] = value
            elif key in ("effort", "serviceTier"):
                old = next(
                    (
                        v.get("settings", {})
                        for v in variants
                        if v["id"] == target.get("variant", "default")
                    ),
                    {},
                )
                field = "reasoningEffort" if key == "effort" else "serviceTier"
                other = "serviceTier" if key == "effort" else "reasoningEffort"
                selected = next(
                    (
                        v
                        for v in variants
                        if v.get("settings", {}).get(field, "default") == value
                        and v.get("settings", {}).get(other, "default")
                        == old.get(other, "default")
                    ),
                    None,
                )
                if selected is None:
                    raise ConversationError(
                        "unsupported",
                        "No native variant preserves the other selected setting",
                    )
                target["variant"] = selected["id"]
            if target.get("variant", "default") not in [v["id"] for v in variants]:
                raise ConversationError(
                    "validation", "Choose an advertised native model variant"
                )
            if target.get("variant") == "default":
                target.pop("variant")
            await a.http(
                "POST", "/api/session/" + identity + "/model", {"model": target}
            )
            try:
                observed = (await a.http("GET", "/api/session/" + identity))[
                    "data"
                ].get("model")
            except Exception as error:
                raise ConversationError(
                    "uncertain",
                    "Native model update readback is unavailable",
                    uncertain=True,
                ) from error
            if not observed or reference(observed) != reference(target):
                raise ConversationError(
                    "uncertain",
                    "Native model settings were acknowledged without matching readback",
                    uncertain=True,
                )
            a.sessions[identity]["model"] = reference(observed)
        else:
            mode = "plan" if info.get("agent") == PLAN_AGENT else "default"
            if key == "mode":
                mode = value
            if key == "permissions":
                saved["permission_mode"] = value
            rules = (
                saved["base_rules"]
                if saved["permission_mode"] == "native"
                else self.permission_rules(saved["permission_mode"])
            )
            rules = deepcopy(rules) + (deepcopy(PLAN_RULES) if mode == "plan" else [])
            changed = False
            try:
                await a.http(
                    "PATCH", "/api/session/" + identity, {"permissions": rules}
                )
                changed = True
                if key == "mode":
                    await a.http(
                        "POST",
                        "/api/session/" + identity + "/agent",
                        {
                            "agent": PLAN_AGENT
                            if mode == "plan"
                            else saved["default_agent"]
                        },
                    )
                observed = (await a.http("GET", "/api/session/" + identity))["data"]
                if observed.get("permissions", []) != rules or (
                    observed.get("agent") == PLAN_AGENT
                ) != (mode == "plan"):
                    raise ConversationError(
                        "uncertain",
                        "Native policy update lacks matching readback; inspect before retrying",
                        uncertain=True,
                    )
            except Exception as error:
                if changed:
                    raise ConversationError(
                        "uncertain",
                        "Native policy was partly updated; inspect before retrying",
                        uncertain=True,
                    ) from error
                raise
            a.core.store.put("opencode-controls", identity, saved)
            a.sessions[identity]["mode"] = mode
            a.sessions[identity]["access"] = (
                {"permission_mode": saved["permission_mode"]}
                if saved["permission_mode"] != "native"
                else {}
            )
        record = a.core.store.get("session", session["session_id"])
        for field in ("model", "mode", "access"):
            if field in a.sessions[identity]:
                record["settings"][field] = deepcopy(a.sessions[identity][field])
        a.core.store.put("session", session["session_id"], record)
        try:
            result = await self.configuration(session)
        except Exception as error:
            raise ConversationError(
                "uncertain",
                "Native settings changed but the refreshed catalog is unavailable",
                uncertain=True,
            ) from error
        a.core.emit("session.config", result, identity)
        return result
