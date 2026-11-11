"""Guarded resource omission between handshake and the first native session."""
from .models import ConversationError, canonical

VERSION = "conversation-manager/resource-policy-1"


def settings_for(prepared, selected):
    policy = prepared.resource_policy
    if not isinstance(policy, dict) or policy.get("schema") != VERSION:
        raise ConversationError("unsupported", "This descriptor does not permit resource finalization")
    items = policy.get("items", [])
    ids = [row["id"] for row in items]
    if len(set(ids)) != len(ids) or not isinstance(selected, list) or len(set(selected)) != len(selected) or set(selected) - set(ids):
        raise ConversationError("validation", "Finalization may only select captured resources")
    if any(row["required"] and row["id"] not in selected for row in items):
        raise ConversationError("conflict", "Required captured resources cannot be omitted")
    settings = {k: v for k, v in prepared.settings.items() if k not in ("instructions", "skills", "mcp")}
    chosen = [row for row in items if row["id"] in selected]
    if any(row["kind"] == "instructions" for row in chosen):
        settings["instructions"] = prepared.settings["instructions"]
    skills = {row["key"] for row in chosen if row["kind"] == "skills"}
    if skills:
        settings["skills"] = [row for row in prepared.settings.get("skills", []) if row["export"] in skills]
    servers = {row["key"] for row in chosen if row["kind"] == "mcp"}
    if servers or prepared.settings.get('mcp', {}).get('selection_digest'):
        original = prepared.settings["mcp"]
        settings["mcp"] = {**original, "servers": [row for row in original.get("servers", []) if row.get("server_name", row.get("name")) in servers]}
        if "native_config" in original:
            settings["mcp"]["native_config"] = {key: value for key, value in original["native_config"].items()
                if any(key == "mcp_servers." + name or key.startswith("mcp_servers." + name + ".") for name in servers)}
    return settings


async def assess(core):
    prepared = core.prepared
    finalized = core.store.get("resource-finalization", core.ref["connection_id"])
    if finalized:
        return finalized["assessment"]
    if prepared.resource_policy is None:
        raise ConversationError("unsupported", "Legacy descriptors retain strict resource delivery")
    result = []
    for row in prepared.resource_policy["items"]:
        supported, reason = True, None
        try:
            operation = {"instructions": "instructions_delivery", "skills": "skills_delivery", "mcp": "mcp_delivery"}[row["kind"]]
            core.supported(operation)
            # Assessment deliberately excludes unrelated required resources.
            from dataclasses import replace
            candidate = replace(prepared, resource_policy={"schema": VERSION, "items": [{**row, "required": False}]})
            settings = settings_for(candidate, [row["id"]])
            core.adapter.validate_settings(settings)
            if row["kind"] == "skills" and prepared.protocol == "codex-app-server":
                from .skill_delivery import require_native_catalog
                await require_native_catalog(core.adapter.rpc, prepared.cwd, settings["skills"])
        except ConversationError as error:
            if error.code != "unsupported" or error.uncertain:
                raise
            supported, reason = False, error.message
        result.append({"id": row["id"], "supported": supported, "reason": reason})
    return {"schema": "conversation-manager/resource-assessment-1", "items": result}


async def finalize(core, params):
    if core.sessions or core.store.get("resource-finalization", core.ref["connection_id"]):
        raise ConversationError("conflict", "Resources have already been finalized or a session exists")
    settings = settings_for(core.prepared, params.get("selected"))
    assessment = await assess(core)
    unavailable = {row["id"] for row in assessment["items"] if not row["supported"]}
    if unavailable & set(params["selected"]):
        raise ConversationError("unsupported", "Selected resources are unsupported by the negotiated backend")
    core.adapter.validate_settings(settings)
    snapshot = params.get("resource_snapshot_ref")
    if not isinstance(snapshot, str) or not snapshot.startswith("sha256:") or len(snapshot) != 71:
        raise ConversationError("validation", "Finalized resources require a content-addressed snapshot")
    row = {"settings": settings, "resource_snapshot_ref": snapshot, "selected": params["selected"], "assessment": assessment}
    canonical(row)
    core.store.put("resource-finalization", core.ref["connection_id"], row)
    core.prepared.settings = settings
    core.prepared.resource_snapshot_ref = snapshot
    return row
