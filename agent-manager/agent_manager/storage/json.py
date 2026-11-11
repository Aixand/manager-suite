"""Canonical JSON and verified local record reads."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from agent_manager.errors import ResourceError


def canonical(value):
    def check(item):
        if isinstance(item, dict):
            if any(not isinstance(k, str) for k in item):
                raise ResourceError("validation", "Resource JSON keys must be strings")
            for v in item.values():
                check(v)
        elif isinstance(item, (list, tuple)):
            for v in item:
                check(v)

    check(value)
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ResourceError(
            "validation", "Resource records require finite UTF-8 JSON"
        ) from exc


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def read(path):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ValueError("duplicate key")
            out[k] = v
        return out

    try:
        raw = Path(path).read_bytes()
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("record too large")
        row = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        canonical(row)
        return row
    except (OSError, ValueError, UnicodeError) as exc:
        raise ResourceError(
            "integrity", "Cannot read the retained resource record"
        ) from exc

