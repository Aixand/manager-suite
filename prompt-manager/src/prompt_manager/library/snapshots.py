"""Capture and select complete prompt snapshots before rendering."""

import os
from copy import deepcopy
from pathlib import Path

from ..codec import MAX_BYTES, canonical, clone, digest
from ..errors import require
from ..prompts.definitions import RENDERER, SNAPSHOT_SCHEMA
from ..prompts.examples import check_examples
from ..prompts.rendering import render
from ..prompts.validation import check_manifest, validate_snapshot
from ..sources.filesystem import capture_files, discover
from ..storage.files import read_json


def read_draft(path, store):
    root = Path(path).resolve()
    pack = read_json(root / "pack.json")
    check_manifest(pack)
    files = capture_files(root, pack)
    dependencies = {}
    for alias, ref in pack.get("dependencies", {}).items():
        snapshot, identity = store.load(ref)
        dependencies[alias] = {"digest": identity["digest"], "snapshot": snapshot}
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "renderer": RENDERER,
        "pack": pack,
        "files": files,
        "dependencies": dependencies,
    }
    require(len(canonical(snapshot)) <= MAX_BYTES, "Snapshot exceeds 32 MiB", "size")
    validate_snapshot(snapshot)
    return snapshot


def load(store, packs, source, prompt=None):
    explicit_path = isinstance(source, os.PathLike)
    source = str(source)
    selected = prompt
    reference, separator, embedded = source.partition("/")
    if "@" in reference and not reference.startswith(".") and not explicit_path:
        if separator:
            require(selected is None or selected == embedded, "Conflicting prompt selections")
            selected = embedded
        snapshot, identity = store.load(reference)
    else:
        path = Path(source)
        if not path.is_dir():
            drafts = discover(packs)
            require(source in drafts, f"Draft pack not found: {source}", "not_found")
            path = drafts[source][0]
        snapshot = read_draft(path, store)
        identity = {
            "name": snapshot["pack"]["name"],
            "version": None,
            "digest": digest(snapshot),
        }
    if selected is not None:
        require(
            isinstance(selected, str) and selected in snapshot["pack"]["prompts"],
            f"Unknown prompt: {selected}",
            "not_found",
        )
    return snapshot, identity, selected


def select(snapshot, selected):
    if selected is not None:
        return selected
    prompts = list(snapshot["pack"]["prompts"])
    require(len(prompts) == 1, "Select a prompt explicitly; the pack has zero or multiple prompts")
    return prompts[0]


def render_source(store, packs, source, inputs=None, *, prompt=None, format="text"):
    snapshot, identity, selected = load(store, packs, source, prompt)
    selected = select(snapshot, selected)
    return render(snapshot, selected, clone({} if inputs is None else inputs), identity, format)


def capture_publisher(source, store):
    require(isinstance(source, (str, os.PathLike)), "Publisher source must be a filesystem path")
    if Path(source).is_file():
        return accept_tree(read_json(source))
    snapshot = read_draft(Path(source), store)
    require(
        digest(read_draft(Path(source), store)) == digest(snapshot),
        "Publisher content changed during import",
        "conflict",
    )
    check_examples(snapshot)
    return snapshot


def accept_tree(tree):
    require(isinstance(tree, dict), "Prompt package must be an object")
    snapshot = deepcopy(tree)
    require(len(canonical(snapshot)) <= MAX_BYTES, "Managed snapshot exceeds size limit", "size")
    validate_snapshot(snapshot)
    check_examples(snapshot)
    return snapshot
