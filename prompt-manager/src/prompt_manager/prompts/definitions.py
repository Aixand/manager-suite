"""Prompt identities, reference grammar, and schema constants."""

import re
from pathlib import PurePosixPath

from ..errors import require

NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
VARIABLE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*\Z")
VERSION = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
ROLES = {"system", "user", "assistant"}
SNAPSHOT_SCHEMA = "prompt-manager/snapshot-1"
RENDERER = "prompt-manager/render-1"


def fields(value, allowed, required, where):
    require(isinstance(value, dict), f"{where}: expected an object")
    require(
        not (set(value) - set(allowed)),
        f"{where}: unknown fields {sorted(set(value) - set(allowed))}",
    )
    require(
        not (set(required) - set(value)),
        f"{where}: missing fields {sorted(set(required) - set(value))}",
    )


def name(value, where, pattern=NAME):
    require(isinstance(value, str) and pattern.fullmatch(value), f"{where}: invalid name {value!r}")


def release_ref(ref):
    require(
        isinstance(ref, str) and ref.count("@") == 1,
        "Expected a pack@version or pack@sha256:digest reference",
    )
    pack, version = ref.split("@")
    name(pack, "release pack")
    require(
        VERSION.fullmatch(version) or DIGEST.fullmatch(version),
        f"Invalid release version: {version}",
    )
    return pack, version


def asset_path(value):
    path = PurePosixPath(value)
    require(
        not path.is_absolute()
        and path.parts
        and ".." not in path.parts
        and str(path) == value
        and "\\" not in value,
        f"Asset path must be a normalized relative path: {value}",
    )
    return path
