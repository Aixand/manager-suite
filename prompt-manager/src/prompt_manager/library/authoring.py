"""Inspect and safely edit authoritative filesystem drafts."""

import os
import tempfile
from pathlib import Path

from ..codec import clone, digest
from ..errors import require
from ..prompts.definitions import asset_path
from ..prompts.examples import check_examples
from ..prompts.validation import check_manifest
from ..sources.exports import write_draft
from ..storage.files import read_json, sync_directory
from ..storage.locks import lock
from .discovery import source as catalog_source
from .snapshots import load, read_draft


def show_catalog(store, packs, registry, identifier, *, prompt=None):
    row, source = catalog_source(store, packs, registry, identifier)
    if row["kind"] == "draft" and row["status"] == "invalid":
        manifest = read_json(source / "pack.json")
        check_manifest(manifest)
        files = {}
        for kind in ("sections", "documents"):
            for asset in manifest.get(kind, {}).values():
                path = (source / asset_path(asset["file"])).resolve()
                require(path.is_relative_to(source.resolve()), "Asset leaves pack directory")
                require(path.stat().st_size <= 32 * 1024 * 1024, "Asset exceeds 32 MiB", "size")
                files[asset["file"]] = path.read_text(encoding="utf-8")
        require(prompt is None or prompt in manifest["prompts"], "Unknown prompt", "not_found")
        return {
            "entry": row,
            "release": {"name": manifest["name"], "version": None, "digest": None},
            "manifest": manifest,
            "prompt": manifest["prompts"].get(prompt),
            "files": files,
            "dependencies": manifest.get("dependencies", {}),
            "inspection_error": row["error"],
        }
    return {"entry": row, **show(store, packs, source, prompt=prompt)}


def save_catalog_file(store, packs, registry, identifier, file, text, *, expected_digest):
    """Validate a staged edit and atomically replace one existing source file."""
    row, source = catalog_source(store, packs, registry, identifier)
    require(row["kind"] == "draft", "Released content is immutable", "conflict")
    require(isinstance(text, str), "File content must be text")
    with lock(store.home, "registry"), lock(store.home, str(source)):
        row, current_source = catalog_source(store, packs, registry, identifier)
        require(
            current_source == source and row.get("editable", True),
            "Source is read-only or changed; select a writable authoring source",
            "conflict",
        )
        snapshot = read_draft(source, store)
        require(
            digest(snapshot) == expected_digest,
            "Draft changed since it was opened; refresh before saving",
            "conflict",
        )
        require(
            file == "pack.json" or file in snapshot["files"],
            "Only existing pack assets may be edited",
        )
        relative = asset_path(file)
        destination = (source / relative).resolve()
        require(destination.is_relative_to(source.resolve()), "Asset leaves pack directory")
        with tempfile.TemporaryDirectory(prefix="prompt-edit-") as temporary:
            staged = Path(temporary)
            write_draft(snapshot, staged)
            (staged / relative).write_text(text, encoding="utf-8")
            read_draft(staged, store)
        fd, temporary = tempfile.mkstemp(prefix=".prompt-edit-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(text.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            sync_directory(destination.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return show_catalog(store, packs, registry, identifier)


def show(store, packs, source, *, prompt=None):
    snapshot, identity, selected = load(store, packs, source, prompt)
    return {
        "release": identity,
        "manifest": clone(snapshot["pack"]),
        "prompt": clone(snapshot["pack"]["prompts"][selected]) if selected else None,
        "files": clone(snapshot["files"]),
        "dependencies": {
            alias: {"name": row["snapshot"]["pack"]["name"], "digest": row["digest"]}
            for alias, row in snapshot["dependencies"].items()
        },
    }


def validate(store, packs, source):
    snapshot, identity, _ = load(store, packs, source)
    return {
        "valid": True,
        "release": identity,
        "prompts": sorted(snapshot["pack"]["prompts"]),
        "examples_passed": check_examples(snapshot),
    }
