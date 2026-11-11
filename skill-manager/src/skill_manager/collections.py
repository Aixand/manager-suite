"""Bounded local skill collection discovery and explicit snapshot imports."""

import os
import stat
from builtins import all as builtins_all

from . import fs
from .codec import digest
from .errors import SkillError, require

IGNORED = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "node_modules",
    "__pycache__",
    ".skill-manager",
}


MAX_IMPORT_BYTES = fs.MAX_TOTAL


def discover(root):
    root = fs.absolute(root)
    candidates, diagnostics = [], []
    count = 0

    def walk(directory, fd, depth):
        nonlocal count
        names = sorted(os.listdir(fd))
        count += len(names)
        require(
            count <= 10000 and depth <= 16,
            "Collection discovery exceeds limits; select a narrower directory",
            "size",
        )
        if "SKILL.md" in names:
            relative = directory.relative_to(root).as_posix()
            try:
                snapshot = fs.capture(directory)
                row = {
                    "path": relative,
                    "name": directory.name,
                    "digest": snapshot["digest"],
                    "valid": snapshot["error"] is None,
                    "error": snapshot["error"],
                    "description": (snapshot["manifest"]["metadata"] or {}).get(
                        "description", ""
                    ),
                }
            except (SkillError, OSError) as error:
                row = {
                    "path": relative,
                    "name": directory.name,
                    "valid": False,
                    "digest": None,
                    "error": str(error),
                    "description": "",
                }
            candidates.append(row)
            require(
                len(candidates) <= 256,
                "Too many skills; select a narrower collection",
                "size",
            )
            return
        for name in names:
            if name in IGNORED:
                continue
            info = fs.stat_at(fd, name)
            path = directory / name
            if (
                stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
            ):
                diagnostics.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "error": "Linked paths are not traversed",
                    }
                )
            elif stat.S_ISDIR(info.st_mode):
                with fs.directory_at(fd, name) as child:
                    walk(path, child, depth + 1)

    with fs.directory(root) as fd:
        walk(root, fd, 0)
    result = {
        "schema": "skill-manager/collection-1",
        "root": str(root),
        "candidates": candidates,
        "diagnostics": diagnostics,
    }
    return {**result, "revision": digest(result)}


def import_selected(
    manager, root, selections=None, *, all=False, expected_revision=None
):
    found = discover(root)
    require(
        expected_revision is None or expected_revision == found["revision"],
        "Collection changed; rediscover before importing",
        "conflict",
    )
    require(
        type(all) is bool and not (all and selections is not None),
        "Use selections or all",
    )
    if all:
        selections = [row["path"] for row in found["candidates"]]
    require(
        isinstance(selections, (list, tuple))
        and selections
        and builtins_all(isinstance(path, str) for path in selections)
        and len(set(selections)) == len(selections),
        "Select distinct collection package paths",
    )
    by_path = {row["path"]: row for row in found["candidates"]}
    require(
        builtins_all(path in by_path for path in selections),
        "Selection is not part of this collection",
    )
    sources = manager._sources()
    captured, total, entries = [], 0, 0
    for relative in selections:
        row = by_path[relative]
        require(row["valid"], f"Invalid selected skill {relative}: {row['error']}")
        path = fs.absolute(root) if relative == "." else fs.absolute(root) / relative
        snapshot = sources.backend.capture(path)
        total += sum(item["size"] for item in snapshot["manifest"]["files"])
        entries += len(snapshot["manifest"]["files"]) + len(
            snapshot["manifest"]["directories"]
        )
        require(
            total <= MAX_IMPORT_BYTES and entries <= 10000,
            "Selected collection exceeds import bounds; import smaller batches",
            "size",
        )
        require(
            snapshot["digest"] == row["digest"],
            "Collection source changed while importing",
            "conflict",
        )
        captured.append((relative, path, snapshot))
    return {
        "schema": "skill-manager/collection-import-1",
        "root": found["root"],
        "items": [
            {"path": relative, "status": sources.import_captured(path, snapshot)}
            for relative, path, snapshot in captured
        ],
    }
