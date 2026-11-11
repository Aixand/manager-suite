"""Native acquisition records and source-tree record compatibility."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import PurePosixPath

MAX_DOWNLOAD = 64 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_ENTRIES = 10000
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class SourceError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

    def to_dict(self):
        return {"code": self.code, "message": str(self)}


def require(condition, message, code="validation"):
    if not condition:
        raise SourceError(code, message)


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def loads(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON key", "integrity")
            result[key] = value
        return result

    try:
        return json.loads(
            data,
            object_pairs_hook=pairs,
            parse_constant=lambda _: require(False, "Non-finite JSON", "integrity"),
        )
    except (ValueError, UnicodeError) as error:
        raise SourceError("validation", "Invalid UTF-8 JSON") from error


def read(path, default=None):
    from . import fs

    try:
        return loads(fs.read_path(path, limit=MAX_TOTAL * 2))
    except FileNotFoundError:
        return default


def atomic(path, value):
    from . import fs

    fs.make_directories(path.parent)
    fs.write_atomic(path, canonical(value) + b"\n")


def relative(value, *, empty=False):
    require(
        isinstance(value, str)
        and len(value) <= 1024
        and "\\" not in value
        and "\0" not in value
        and ":" not in value,
        "Invalid package path",
    )
    if value == "" and empty:
        return ""
    p = PurePosixPath(value)
    require(
        value
        and not p.is_absolute()
        and all(part not in ("", ".", "..") for part in value.split("/"))
        and str(p) == value,
        "Package path must stay inside its root",
    )
    return value


def identifier(value):
    require(
        isinstance(value, str) and IDENTIFIER.fullmatch(value),
        "Invalid resource identifier",
    )
    return value


def slug(value):
    text = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    if not text or not text[0].isalpha():
        text = "resource-" + text
    return text[:45].rstrip("-_") or "resource"


def validate_source(source):
    from .github_source import COMMIT, parse_url

    fields = {
        "schema",
        "kind",
        "repository_id",
        "repository",
        "url",
        "ref",
        "path",
        "commit",
        "pinned",
        "source_digest",
        "candidate_id",
        "role",
        "label",
        "interval_hours",
    }
    require(
        isinstance(source, dict) and fields <= set(source) <= fields | {"tree_sha"},
        "Invalid source descriptor",
        "integrity",
    )
    require(
        source["schema"] == "resource-sources/github-1" and source["kind"] == "github",
        "Unsupported source descriptor",
        "integrity",
    )
    require(
        isinstance(source["repository_id"], str)
        and re.fullmatch(r"[1-9][0-9]{0,31}", source["repository_id"]),
        "Invalid repository identity",
        "integrity",
    )
    owner, repo, tail = parse_url(source["url"])
    require(
        not tail and source["repository"] == owner + "/" + repo,
        "Repository source mismatch",
        "integrity",
    )
    require(
        isinstance(source["ref"], str)
        and 0 < len(source["ref"]) <= 255
        and isinstance(source["commit"], str)
        and COMMIT.fullmatch(source["commit"])
        and isinstance(source["source_digest"], str)
        and DIGEST.fullmatch(source["source_digest"]),
        "Invalid source reference",
        "integrity",
    )
    require(
        type(source["pinned"]) is bool
        and source["role"] in ("system", "user", "assistant")
        and type(source["interval_hours"]) is int
        and 1 <= source["interval_hours"] <= 720,
        "Invalid source update policy",
        "integrity",
    )
    relative(source["path"], empty=True)
    require(
        all(
            isinstance(source[k], str) and 0 < len(source[k]) <= limit
            for k, limit in [("candidate_id", 1536), ("label", 128)]
        ),
        "Invalid source candidate",
        "integrity",
    )
    if "tree_sha" in source:
        require(
            isinstance(source["tree_sha"], str)
            and COMMIT.fullmatch(source["tree_sha"]),
            "Invalid source tree identity",
            "integrity",
        )
    return source


def validate_tree(tree):
    require(
        isinstance(tree, dict)
        and tree.get("schema") == "resource-sources/tree-1"
        and isinstance(tree.get("files"), dict)
        and isinstance(tree.get("directories"), list),
        "Invalid source tree",
        "integrity",
    )
    require(
        len(tree["files"]) + len(tree["directories"]) <= MAX_ENTRIES,
        "Too many source entries",
    )
    total, folded = 0, set()
    require(
        len(set(tree["directories"])) == len(tree["directories"]),
        "Duplicate source directories",
    )
    all_directories = set(tree["directories"])
    for path in [*tree["directories"], *tree["files"]]:
        relative(path)
        all_directories.update(
            str(parent) for parent in PurePosixPath(path).parents if str(parent) != "."
        )
    for path in [*all_directories, *tree["files"]]:
        relative(path)
        require(path.casefold() not in folded, "Source contains colliding paths")
        folded.add(path.casefold())
    for path, row in tree["files"].items():
        require(
            isinstance(row, dict)
            and set(row) == {"content_b64", "executable"}
            and type(row["executable"]) is bool,
            "Invalid source file",
            "integrity",
        )
        try:
            data = base64.b64decode(row["content_b64"], validate=True)
        except (ValueError, TypeError) as error:
            raise SourceError("integrity", "Invalid source file encoding") from error
        total += len(data)
        require(
            len(data) <= MAX_FILE and total <= MAX_TOTAL, "Source exceeds size limits"
        )
        parents = PurePosixPath(path).parents
        require(
            not any(str(p) in tree["files"] for p in parents),
            "Source file is also a directory",
        )
    return tree


def subtree(tree, path):
    relative(path, empty=True)
    if not path:
        return tree
    prefix = path + "/"
    return {
        "schema": tree["schema"],
        "files": {
            key[len(prefix) :]: value
            for key, value in tree["files"].items()
            if key.startswith(prefix)
        },
        "directories": [
            key[len(prefix) :] for key in tree["directories"] if key.startswith(prefix)
        ],
    }
