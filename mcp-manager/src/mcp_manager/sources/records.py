"""Bounded JSON, contained paths, atomic records and immutable source trees."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re

from ..errors import McpError as SourceError, require
from ..storage.files import atomic as write_bytes

MAX_DOWNLOAD = 64 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_ENTRIES = 10000
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


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
    try:
        require(
            path.stat().st_size <= MAX_TOTAL * 2,
            "Record exceeds size limit",
            "integrity",
        )
        return loads(path.read_bytes())
    except FileNotFoundError:
        return default


def atomic(path, value):
    write_bytes(path, canonical(value) + b"\n")


def relative(value, *, empty=False):
    from github_source.contracts import relative as validate_path
    from github_source import GitHubError

    try:
        return validate_path(value, empty=empty)
    except GitHubError as error:
        raise SourceError(error.code, str(error)) from error


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
    from .github import COMMIT, parse_url

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
    from github_source.contracts import validate_tree as validate_shared_tree
    from github_source import GitHubError

    require(
        isinstance(tree, dict) and tree.get("schema") == "resource-sources/tree-1",
        "Invalid retained source tree",
        "integrity",
    )
    try:
        validate_shared_tree({**tree, "schema": "github-source/tree-1"})
    except GitHubError as error:
        raise SourceError(error.code, str(error)) from error
    # Never normalize or rewrite the object used to calculate a saved digest.
    return tree


def materialize(tree, directory):
    validate_tree(tree)
    directory = Path(directory)
    require(not directory.exists(), "Destination must be a fresh directory", "conflict")
    directory.mkdir(parents=True, mode=0o700)
    for path in tree["directories"]:
        (directory / path).mkdir(parents=True, exist_ok=True)
    for path, row in tree["files"].items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(row["content_b64"], validate=True))
        target.chmod(0o755 if row["executable"] else 0o644)
    return directory
