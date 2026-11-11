"""Guarded display names, reversible archival, and independent personal copies."""

from ..codec import canonical
from ..errors import require
from .metadata import normalize_tags


def _label(value):
    require(
        value is None or (isinstance(value, str) and bool(value.strip()) and len(value) <= 128),
        "Display name must be nonblank text of at most 128 characters, or null",
    )
    canonical(value)


def rename(state, publisher, identifier, label, expected_revision):
    _label(label)
    with state.backend.lock():
        row = state.guarded(publisher, identifier, expected_revision)
        updated = dict(row)
        if label is None:
            updated.pop("label", None)
        else:
            updated["label"] = label
        if updated == row:
            return state.status(publisher, identifier)
        return state.commit(updated, row)


def set_archived(state, publisher, identifier, enabled, expected_revision):
    require(type(enabled) is bool, "archived must be a boolean")
    with state.backend.lock():
        row = state.guarded(publisher, identifier, expected_revision)
        if row.get("archived", False) == enabled:
            return state.status(publisher, identifier)
        updated = dict(row)
        if enabled:
            updated["archived"] = True
        else:
            updated.pop("archived", None)
        return state.commit(updated, row)


def duplicate(state, publisher, identifier, target_id, *, revision=None, label=None):
    state.path(publisher, identifier)
    state.path("local", target_id)
    _label(label)
    with state.backend.lock():
        require(
            state.load("local", target_id, missing_ok=True) is None,
            "Duplicate destination already exists",
            "conflict",
        )
        source = state.load(publisher, identifier, revision)
        snapshot = state.backend.tree(state.backend.snapshot(source["active_digest"]))
        require(
            target_id != snapshot["pack"]["name"],
            "Choose a different name for the duplicate release namespace",
            "conflict",
        )
        # Give the independent copy its own immutable release namespace. Prompt
        # IDs, referenced assets, examples and captured dependencies stay intact.
        snapshot["pack"]["name"] = target_id
        snapshot = state.backend.from_tree(snapshot)
        state.backend.store(snapshot)
        identity = state.backend.snapshot_digest(snapshot)
        baseline = {"digest": identity, "revision": None, "version": None}
        row = {
            "schema": state.backend.schema,
            "publisher_id": "local",
            "id": target_id,
            "baseline": baseline,
            "upstream": dict(baseline),
            "active_digest": identity,
            "sync_error": None,
            "tracking": False,
        }
        if label is not None:
            row["label"] = label
        if source.get("tags"):
            row["tags"] = list(source["tags"])
        return state.commit(row, None, snapshot=snapshot)


def set_tags(state, publisher, identifier, tags, expected_revision):
    tags = normalize_tags(tags)
    with state.backend.lock():
        row = state.guarded(publisher, identifier, expected_revision)
        if row.get("tags", []) == tags:
            return state.status(publisher, identifier)
        updated = dict(row)
        if tags:
            updated["tags"] = tags
        else:
            updated.pop("tags", None)
        return state.commit(updated, row)


def refresh_metadata(state, publisher, identifier, expected_revision):
    with state.backend.lock():
        row = state.guarded(publisher, identifier, expected_revision)
        snapshot = state.backend.snapshot(row["active_digest"])
        if row.get("content_metadata") == state.backend.describe(snapshot, row["active_digest"]):
            return state.status(publisher, identifier)
        return state.commit(row, row, snapshot=snapshot)
