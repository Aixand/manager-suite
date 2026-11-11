"""Discover local drafts and releases without application or managed resolution."""

from pathlib import Path

from ..codec import digest
from ..errors import require
from ..prompts.validation import check_manifest
from ..sources.filesystem import discover
from ..storage.files import read_json
from .snapshots import read_draft


def prompts(manifest):
    return [
        {
            "name": key,
            "description": row.get("description", ""),
            "inputs": row.get("inputs", {}),
            "instruction_compatible": bool(row.get("sections"))
            and all(section.get("role") == "system" for section in row["sections"]),
        }
        for key, row in sorted(manifest["prompts"].items())
    ]


def entries(store, packs, registry):
    """Inspect draft/release entries without resolving unrelated consumers."""
    sources = registry.list("sources")
    if packs:
        sources.append(
            {
                "id": "configured",
                "label": "Configured directories",
                "paths": [str(path) for path in packs],
                "writable": True,
            }
        )
    store_id = digest(str(registry.home))[7:]
    rows, statuses = [], []
    seen = set()
    for source in sources:
        status = {**source, "status": "unavailable" if source.get("error") else "available"}
        statuses.append(status)
        if source.get("error"):
            continue
        for root in map(Path, source["paths"]):
            if not root.is_dir():
                status.update(
                    status="unavailable", error=f"Source directory is unavailable: {root}"
                )
                continue
            paths = (
                [root / "pack.json"]
                if (root / "pack.json").is_file()
                else sorted(root.glob("*/pack.json"))
            )
            for file in paths:
                directory = str(file.parent.resolve())
                if directory in seen:
                    continue
                seen.add(directory)
                row = {
                    "id": digest([store_id, "draft", directory])[7:],
                    "kind": "draft",
                    "source_id": source["id"],
                    "source": source["label"],
                    "source_revision": source.get("revision"),
                    "editable": source.get("writable", True),
                    "path": directory,
                    "name": file.parent.name,
                    "version": None,
                    "digest": None,
                    "prompts": [],
                    "status": "available",
                }
                try:
                    manifest = read_json(file)
                    check_manifest(manifest)
                    row.update(
                        name=manifest["name"],
                        description=manifest.get("description", ""),
                        prompts=prompts(manifest),
                    )
                    row["digest"] = digest(read_draft(directory, store))
                except (ValueError, OSError) as error:
                    row.update(status="invalid", error=str(error))
                rows.append(row)
    for file in sorted((store.home / "releases").glob("*/*.json")):
        reference = f"{file.parent.name}@{file.stem}"
        row = {
            "id": digest([store_id, "release", reference])[7:],
            "kind": "release",
            "source_id": "releases",
            "source": "Local releases",
            "editable": False,
            "reference": reference,
            "name": file.parent.name,
            "version": file.stem,
            "digest": None,
            "prompts": [],
            "status": "available",
        }
        try:
            snapshot, identity = store.load(reference)
            row.update(
                **identity,
                description=snapshot["pack"].get("description", ""),
                prompts=prompts(snapshot["pack"]),
            )
        except (ValueError, OSError) as error:
            row.update(status="invalid", error=str(error))
        rows.append(row)
    return rows, statuses


def source(store, packs, registry, identifier):
    rows, _ = entries(store, packs, registry)
    row = next((row for row in rows if row["id"] == identifier), None)
    require(row is not None, "Catalog entry no longer exists; refresh the catalog", "not_found")
    return row, Path(row["path"]) if row["kind"] == "draft" else row["reference"]


def list_packs(store, packs):
    """Discover draft metadata without requiring its dependencies to be released."""
    return {
        "drafts": [
            {"name": name, "path": str(path), "prompts": sorted(pack["prompts"])}
            for name, (path, pack) in sorted(discover(packs).items())
        ],
        "releases": store.list(),
    }
