"""Bounded JSON for owned definitions; stored digests remain unchanged."""
import hashlib
import json

from ..errors import McpError, require

MAX_BYTES = 262144


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise McpError("validation", "MCP content must be finite UTF-8 JSON") from error


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def bounded(value):
    require(len(canonical(value)) <= MAX_BYTES, "MCP definition exceeds 256 KiB", "size")
    return value
