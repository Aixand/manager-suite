"""Strict JSON and deterministic UTF-8 identities."""

import hashlib
import json

from .errors import PromptError, require

MAX_BYTES = 32 * 1024 * 1024


def _check_json(value):
    if isinstance(value, dict):
        require(
            all(isinstance(key, str) for key in value), "JSON object keys must be strings", "json"
        )
        for child in value.values():
            _check_json(child)
    elif isinstance(value, list):
        for child in value:
            _check_json(child)
    else:
        require(
            value is None or type(value) in (str, bool, int, float),
            f"Unsupported JSON value type: {type(value).__name__}",
            "json",
        )


def canonical(value):
    try:
        _check_json(value)
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise PromptError("json", f"Expected finite, UTF-8 JSON data: {exc}") from exc


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, f"Duplicate JSON key: {key}", "json")
        result[key] = value
    return result


def _constant(value):
    raise PromptError("json", f"Non-finite JSON value: {value}")


def parse(raw, origin="JSON"):
    try:
        result = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        canonical(result)  # Reject overflowed numbers and unpaired surrogates too.
        return result
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PromptError("json", f"{origin}: {exc}") from exc


def clone(value):
    return parse(canonical(value))
