"""Bounded tag policy and content-linked descriptive metadata."""

from collections.abc import Sequence

from ..codec import canonical
from ..errors import require
from ..prompts.definitions import NAME


def normalize_tags(values, *, code="validation"):
    require(
        isinstance(values, Sequence)
        and not isinstance(values, (str, bytes, bytearray))
        and len(values) <= 32,
        "Tags must be a sequence of at most 32 strings",
        code,
    )
    require(all(isinstance(value, str) for value in values), "Tags must be text", code)
    tags = sorted({value.strip().casefold() for value in values})
    require(
        all(0 < len(tag) <= 64 for tag in tags),
        "Tags must be nonblank and at most 64 characters after normalization",
        code,
    )
    canonical(tags)
    return tags


def describe(snapshot, identity):
    return {
        "digest": identity,
        "name": snapshot["pack"]["name"],
        "description": snapshot["pack"].get("description", ""),
    }


def validate_description(value, identity):
    require(
        isinstance(value, dict)
        and set(value) == {"digest", "name", "description"}
        and value["digest"] == identity
        and isinstance(value["name"], str)
        and NAME.fullmatch(value["name"])
        and isinstance(value["description"], str),
        "Invalid or stale managed content metadata",
        "integrity",
    )
