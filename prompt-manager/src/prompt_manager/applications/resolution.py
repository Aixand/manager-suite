"""Capture an effective application prompt and its exact provenance."""

from pathlib import Path

from ..codec import clone, digest
from ..errors import PromptError, require
from ..library.snapshots import load
from ..managed.inspection import resolve
from ..prompts.definitions import fields, name
from ..prompts.rendering import render


def capture(store, packs, registry, managed, identifier, logical_prompt, fallback=None):
    """Return the inspected provenance and the very snapshot it describes."""
    name(identifier, "application identifier")
    name(logical_prompt, "logical prompt")
    try:
        application = registry.get("applications", identifier)
    except PromptError as error:
        if error.code != "not_found":
            raise
        application = None
    selection = application["prompts"].get(logical_prompt) if application else None
    used_fallback = selection is None
    source_id = catalog_entry_id = path = reference = None
    editable = False
    store_id = digest(str(store.home))[7:]
    if used_fallback:
        require(
            fallback is not None
            and (application is None or application["fallback_policy"] == "packaged"),
            f"No effective prompt for {identifier}/{logical_prompt}; explicit fallback required",
            "not_found",
        )
        fallback = clone(fallback)
        fields(fallback, {"pack_path", "prompt"}, {"pack_path", "prompt"}, "packaged fallback")
        name(fallback["prompt"], "fallback prompt")
        require(
            isinstance(fallback["pack_path"], str)
            and "\0" not in fallback["pack_path"]
            and Path(fallback["pack_path"]).is_absolute(),
            "Fallback pack path must be absolute and contain no NUL characters",
        )
        path = str(Path(fallback["pack_path"]).resolve())
        require(Path(path).is_dir(), f"Packaged fallback is unavailable: {path}", "not_found")
        selection = {"kind": "packaged", "pack_path": path, "prompt": fallback["prompt"]}
        snapshot, identity, selected = load(store, packs, Path(path), fallback["prompt"])
    elif selection["kind"] == "managed":
        owned = resolve(
            managed,
            selection["publisher_id"],
            selection["package_id"],
            revision=selection.get("revision"),
        )
        snapshot, selected = owned["snapshot"], selection["prompt"]
        require(selected in snapshot["pack"]["prompts"], "Unknown managed prompt", "not_found")
        identity = {"name": snapshot["pack"]["name"], "version": None, "digest": owned["digest"]}
        source_id = selection["publisher_id"]
        catalog_entry_id = digest([store_id, "managed", source_id, selection["package_id"]])[7:]
        editable = "revision" not in selection
    elif selection["kind"] == "release":
        reference = selection["reference"]
        snapshot, identity, selected = load(store, packs, reference)
        source_id = "releases"
        if identity["version"] is not None:
            catalog_entry_id = digest([store_id, "release", reference.partition("/")[0]])[7:]
    else:
        source_id = selection["source_id"]
        registration = registry.get("sources", source_id)
        directory = Path(selection["pack_path"])
        require(
            directory.resolve() == directory,
            "Registered pack path now resolves elsewhere; update the selection explicitly",
            "conflict",
        )
        require(
            any(directory.is_relative_to(Path(root).resolve()) for root in registration["paths"]),
            "Selected pack is outside its registered source",
            "conflict",
        )
        require(directory.is_dir(), f"Registered pack is unavailable: {directory}", "not_found")
        path = str(directory)
        # Catalog discovery remains direct packs or their immediate children.
        roots = list(packs)
        for registered in registry.list("sources"):
            if not registered.get("error"):
                roots.extend(Path(root) for root in registered["paths"])
        if any(
            directory == root or (directory.parent == root and not (root / "pack.json").is_file())
            for root in roots
        ):
            catalog_entry_id = digest([store_id, "draft", path])[7:]
        editable = registration["writable"]
        snapshot, identity, selected = load(store, packs, directory, selection["prompt"])
    resolution = {
        "schema": "prompt-manager/application-resolution-1",
        "application_id": identifier,
        "application_revision": application["revision"] if application else None,
        "logical_prompt": logical_prompt,
        "target": clone(selection),
        "store": str(store.home),
        "store_id": store_id,
        "source_id": source_id,
        "catalog_entry_id": catalog_entry_id,
        "path": path,
        "reference": reference,
        "editable": editable,
        "release": dict(identity),
        "dependencies": {
            alias: {"name": child["snapshot"]["pack"]["name"], "digest": child["digest"]}
            for alias, child in snapshot["dependencies"].items()
        },
        "used_fallback": used_fallback,
    }
    if selection["kind"] == "managed":
        resolution["managed"] = owned["resolution"]
    return resolution, snapshot, identity, selected


def render_application(
    store,
    packs,
    registry,
    managed,
    identifier,
    logical_prompt,
    inputs=None,
    *,
    format="text",
    fallback=None,
):
    resolution, snapshot, identity, selected = capture(
        store, packs, registry, managed, identifier, logical_prompt, fallback
    )
    result = render(snapshot, selected, clone({} if inputs is None else inputs), identity, format)
    result["schema"] = "prompt-manager/application-render-result-1"
    result["resolution"] = resolution
    return result
