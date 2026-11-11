"""Discover native skills inside an already captured repository tree."""

import base64
import re
from pathlib import PurePosixPath

from ._source_records import relative, require, slug, subtree


def text(tree, path):
    require(path in tree["files"], f"Source file missing: {path}", "not_found")
    try:
        return base64.b64decode(
            tree["files"][path]["content_b64"], validate=True
        ).decode("utf-8")
    except (ValueError, UnicodeError) as error:
        from ._source_records import SourceError

        raise SourceError("validation", "Skill instructions must be UTF-8") from error


def under(path, root):
    return not root or path == root or path.startswith(root + "/")


def discover(kind, tree, source):
    root = source.get("path", "")
    relative(root, empty=True)
    rows = []
    for path in sorted(tree["files"]):
        if not under(path, root):
            continue
        name = PurePosixPath(path).name
        directory = str(PurePosixPath(path).parent)
        directory = "" if directory == "." else directory
        label = PurePosixPath(directory).name or source["repository"].split("/")[-1]
        common = {
            "path": directory,
            "name": slug(label).replace("_", "-"),
            "description": "",
            "kind": kind,
        }
        if name == "SKILL.md":
            body = text(tree, path)
            match = re.search(r"^name:\s*['\"]?([a-z0-9-]+)", body, re.M)
            common["name"] = match.group(1) if match else common["name"]
            desc = re.search(r"^description:\s*(.+)", body, re.M)
            common["description"] = desc.group(1).strip("'\"") if desc else ""
            rows.append(
                {
                    **common,
                    "id": path,
                    "type": "skill",
                    "preview": body[:4096],
                    "preview_truncated": len(body) > 4096,
                }
            )
    require(
        len(rows) <= 256,
        "Too many resources; select a narrower source directory",
        "size",
    )
    for row in rows:
        prefix = row["path"] + "/" if row["path"] else ""
        paths = [p for p in tree["files"] if p.startswith(prefix)]
        row["files"] = paths[:32]
        row["file_count"] = len(paths)
    return rows


def package_tree(tree, candidate, *, name=None, role="system"):
    return subtree(tree, candidate["path"])
