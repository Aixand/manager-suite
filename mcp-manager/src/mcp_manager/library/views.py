"""Bounded shared inspection/review views; mirrored by Skill Manager."""

import base64
import hashlib
import json

from .reconciliation import MISSING, pointer
from ..errors import require


def lookup(tree, path):
    if path == "":
        return tree
    require(isinstance(path, str) and path.startswith("/"), "Invalid resource pointer")
    value = tree
    for part in path[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or key not in value:
            return MISSING
        value = value[key]
    return value


def group(path):
    parts = path.split("/")
    if len(parts) >= 3 and parts[1] in {"files", "dependencies", "directories"}:
        return "/".join(parts[:3])
    return "/definition" if path.startswith("/definition") else "/" + parts[1] if len(parts) > 1 else ""


def label(path):
    if path == "/definition":
        return "server.json"
    if path == "/pack":
        return "pack.json"
    if path.startswith("/files/"):
        return path[7:].replace("~1", "/").replace("~0", "~")
    return path[1:].replace("~1", "/").replace("~0", "~") or "Complete package"


def display(value, *, path="", offset=0, limit=32768, raw=False):
    if value is MISSING:
        return {"present": False}
    executable = value.get("executable") if isinstance(value, dict) else None
    binary = None
    if isinstance(value, dict) and "content_b64" in value:
        binary = base64.b64decode(value["content_b64"], validate=True)
    elif path.endswith("/content_b64") and isinstance(value, str):
        binary = base64.b64decode(value, validate=True)
    if binary is not None:
        return {
            "present": True,
            "binary": True,
            "size": len(binary),
            "digest": "sha256:" + hashlib.sha256(binary).hexdigest(),
            "executable": executable,
        }
    text_value = (
        value.get("text") if not raw and isinstance(value, dict) and "text" in value else value
    )
    text = (
        text_value
        if isinstance(text_value, str)
        else json.dumps(text_value, ensure_ascii=False, indent=2, sort_keys=True)
    )
    chunk = text[offset : offset + limit]
    # Count escaped wire bytes as well as characters (including control/Unicode
    # escapes) so bounded previews remain bounded in JSONL transports.
    wire_limit = max(12, limit)  # A Unicode surrogate pair must still make progress.
    if len(json.dumps(chunk, ensure_ascii=True)) - 2 > wire_limit:
        lo, hi = 0, len(chunk)
        while lo < hi:
            middle = (lo + hi + 1) // 2
            if len(json.dumps(chunk[:middle], ensure_ascii=True)) - 2 <= wire_limit:
                lo = middle
            else:
                hi = middle - 1
        chunk = chunk[:lo]
    return {
        "present": True,
        "binary": False,
        "text": chunk,
        "value_type": "text" if isinstance(text_value, str) else "json",
        "offset": offset,
        "total": len(text),
        "next_offset": offset + len(chunk) if offset + len(chunk) < len(text) else None,
        "executable": executable,
    }


def view(
    managed,
    publisher,
    identifier,
    *,
    review=False,
    path=None,
    expected_revision=None,
    resolutions=None,
    take=None,
    offset=0,
    limit=32768,
    conflict_offset=0,
):
    require(
        type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 32768,
        "Invalid view offset/limit",
    )
    require(type(conflict_offset) is int and conflict_offset >= 0, "Invalid conflict offset")
    row = managed.status(publisher, identifier)
    require(
        expected_revision is None or row["revision"] == expected_revision,
        "Resource changed; refresh the review",
        "conflict",
    )
    active = managed.backend.snapshot(row["active_digest"])
    local = managed.backend.tree(active)
    trees = {"managed": local}
    preview = None
    if review:
        preview = managed.preview(publisher, identifier, resolutions, take)
        require(
            preview["revision"] == row["revision"],
            "Resource changed while preparing review",
            "conflict",
        )
        trees.update(
            {
                "baseline": managed.backend.tree(
                    managed.backend.snapshot(row["baseline"]["digest"])
                ),
                "publisher": managed.backend.tree(
                    managed.backend.snapshot(row["upstream"]["digest"])
                ),
                "proposed": preview["candidate"],
            }
        )
    paths = {"/definition"} if "definition" in local else {pointer("/files", name) for name in local.get("files", {})}
    if "pack" in local:
        paths.add("/pack")
    conflicts, changed = [], {}
    if preview:
        original = managed.preview(publisher, identifier) if resolutions else preview
        require(
            original["revision"] == row["revision"],
            "Resource changed while preparing conflicts",
            "conflict",
        )
        for side in ("managed", "publisher"):
            for change in preview[side + "_changes"]:
                changed.setdefault(group(change["path"]), set()).add(side)
        for conflict in original["conflicts"]:
            grouped = group(conflict["path"])
            changed.setdefault(grouped, set())
            conflicts.append(
                {
                    "path": conflict["path"],
                    "file": grouped,
                    "resolved": bool(resolutions and conflict["path"] in resolutions),
                }
            )
        paths = set(changed)
    files = [
        {
            "path": item,
            "label": label(item),
            "managed_changed": "managed" in changed.get(item, set()),
            "publisher_changed": "publisher" in changed.get(item, set()),
            "conflicts": sum(conflict["file"] == item for conflict in conflicts),
        }
        for item in sorted(paths)
    ]
    result = {
        "status": row,
        "name": active.get("manifest", {}).get(
            "name", local.get("pack", {}).get("name", publisher if identifier == "default" else identifier)
        ),
        "store": str(managed.backend.home),
        "files": files,
        "conflicts": conflicts,
        "valid": preview["valid"] if preview else True,
        "unresolved_count": len(preview["conflicts"]) if preview else 0,
        "validation_error": preview["validation_error"] if preview else None,
    }
    if path is not None:
        require(path in paths, "Unknown review/file path", "not_found")
        result["sides"] = {
            side: display(lookup(tree, path), path=path, offset=offset, limit=limit)
            for side, tree in trees.items()
        }
        selected_conflicts = [conflict for conflict in conflicts if conflict["file"] == path]
        result["conflict_offset"] = conflict_offset
        result["next_conflict_offset"] = (
            conflict_offset + 8 if conflict_offset + 8 < len(selected_conflicts) else None
        )
        result["selected_conflicts"] = [
            {
                **conflict,
                "sides": {
                    side: display(
                        lookup(tree, conflict["path"]),
                        path=conflict["path"],
                        offset=0,
                        limit=min(limit, 8192),
                        raw=True,
                    )
                    for side, tree in trees.items()
                    if side != "proposed"
                },
            }
            for conflict in selected_conflicts[conflict_offset : conflict_offset + 8]
        ]
    return result
