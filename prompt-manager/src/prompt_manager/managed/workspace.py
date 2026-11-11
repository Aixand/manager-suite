"""Read-only candidate validation, historical comparison, and portable export."""

from pathlib import Path

from ..library.comparison import compare_snapshots
from ..prompts.examples import check_examples
from ..storage.objects import publish_immutable


def capture(state, publisher, identifier, revision=None):
    row = state.status(publisher, identifier, revision)
    return row, state.backend.snapshot(row["active_digest"])


def validate(state, publisher, identifier, files=None, *, revision=None):
    row, snapshot = capture(state, publisher, identifier, revision)
    if files is not None:
        snapshot = state.backend.edit(snapshot, files)
    return {
        "schema": "prompt-manager/managed-validation-1",
        "valid": True,
        "revision": row["revision"],
        "digest": state.backend.snapshot_digest(snapshot),
        "prompts": sorted(snapshot["pack"]["prompts"]),
        "examples_passed": check_examples(snapshot),
    }


def compare(
    state,
    publisher,
    identifier,
    before_revision,
    after_revision=None,
    *,
    inputs=None,
    prompt=None,
    format="text",
):
    before, left = capture(state, publisher, identifier, before_revision)
    # Reuse one capture when both sides designate the same observed state.
    if after_revision == before_revision:
        after, right = before, left
    else:
        after, right = capture(state, publisher, identifier, after_revision)

    def identity(row, snapshot):
        return {
            "name": snapshot["pack"]["name"],
            "version": None,
            "digest": row["active_digest"],
            "revision": row["revision"],
        }

    return compare_snapshots(
        left,
        right,
        identity(before, left),
        identity(after, right),
        inputs=inputs,
        left_prompt=prompt,
        right_prompt=prompt,
        format=format,
    )


def export(state, publisher, identifier, destination, *, revision=None):
    row, snapshot = capture(state, publisher, identifier, revision)
    path = Path(destination).resolve()
    # Never silently replace an unrelated file; equal repeated exports are safe.
    publish_immutable(path, snapshot)
    return {
        "schema": "prompt-manager/managed-export-1",
        "path": str(path),
        "revision": row["revision"],
        "digest": row["active_digest"],
    }
