"""Application registration contracts and guarded mapping changes."""

from ..codec import digest
from ..errors import require
from ..prompts.definitions import fields, name
from ..storage.files import atomic_json
from ..storage.locks import lock
from ..storage.records import check_revision
from .targets import target


def descriptor(identifier, prompts, label=None, fallback_policy="none", *, resolve_paths=True):
    name(identifier, "application identifier")
    require(label is None or isinstance(label, str), "Application label must be text")
    require(fallback_policy in ("none", "packaged"), "Fallback policy must be none or packaged")
    require(isinstance(prompts, dict), "Application prompts must be a mapping")
    normalized = {}
    for logical, selection in prompts.items():
        name(logical, "logical prompt")
        normalized[logical] = target(selection, resolve_paths=resolve_paths)
    row = {
        "schema": "prompt-manager/application-1",
        "id": identifier,
        "label": identifier if label is None else label,
        "prompts": normalized,
        "fallback_policy": fallback_policy,
    }
    return {**row, "revision": digest(row)}


def validate_descriptor(row):
    keys = {"schema", "id", "label", "prompts", "fallback_policy", "revision"}
    fields(row, keys, keys, "application registration")
    require(row["schema"] == "prompt-manager/application-1", "Invalid application schema")
    normalized = descriptor(
        row["id"], row["prompts"], row["label"], row["fallback_policy"], resolve_paths=False
    )
    require(row == normalized, "Application registration revision or content mismatch", "integrity")
    return normalized


def application(
    registry, identifier, *, prompts, label=None, fallback_policy="none", expected_revision=None
):
    row = descriptor(identifier, prompts, label, fallback_policy)
    with lock(registry.home, "registry"):
        check_revision(registry.get("applications", identifier, missing_ok=True), expected_revision)
        atomic_json(registry.home / "applications" / f"{identifier}.json", row)
    return row


def set_prompt(registry, identifier, logical_prompt, selection, *, expected_revision):
    name(identifier, "application identifier")
    name(logical_prompt, "logical prompt")
    selection = target(selection) if selection is not None else None
    with lock(registry.home, "registry"):
        current = registry.get("applications", identifier)
        check_revision(current, expected_revision)
        prompts = current["prompts"]
        if selection is None:
            prompts.pop(logical_prompt, None)
        else:
            prompts[logical_prompt] = selection
        row = descriptor(
            identifier,
            prompts,
            current["label"],
            current["fallback_policy"],
            resolve_paths=False,
        )
        atomic_json(registry.home / "applications" / f"{identifier}.json", row)
    return row
