"""Atomic edits to accepted manager-owned prompt content."""

from copy import deepcopy

from ..codec import parse
from ..errors import require
from ..prompts.definitions import asset_path


def edit_snapshot(backend, snapshot, files):
    require(isinstance(files, dict), "Files must map relative paths to text or null")
    original = deepcopy(snapshot["pack"])
    for path, text in files.items():
        require(isinstance(path, str), "Prompt file paths must be text")
        asset_path(path)
        require(text is None or isinstance(text, str), "Prompt file content must be text or null")
        if path == "pack.json":
            require(text is not None, "Cannot delete pack.json")
            snapshot["pack"] = parse(text, "managed pack.json")
        elif text is None:
            snapshot["files"].pop(path, None)
        else:
            snapshot["files"][path] = text.replace("\r\n", "\n").replace("\r", "\n")
    require(
        isinstance(snapshot["pack"], dict) and snapshot["pack"].get("name") == original["name"],
        "Editing cannot rename a managed prompt pack",
    )
    snapshot["dependencies"] = {
        alias: snapshot["dependencies"][alias]
        if original.get("dependencies", {}).get(alias) == reference
        else backend._dependency(reference)
        for alias, reference in snapshot["pack"].get("dependencies", {}).items()
    }
    return backend.from_tree(snapshot)


def save(state, publisher, identifier, files, expected_revision):
    with state.backend.lock():
        row = state.guarded(publisher, identifier, expected_revision)
        snapshot = state.backend.edit(state.backend.snapshot(row["active_digest"]), files)
        state.backend.store(snapshot)
        identity = state.backend.snapshot_digest(snapshot)
        if identity == row["active_digest"]:
            return state.status(publisher, identifier)
        return state.commit({**row, "active_digest": identity}, row, snapshot=snapshot)
