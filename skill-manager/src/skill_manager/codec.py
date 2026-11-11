"""Strict JSON and identities. Binary digests always hash original bytes."""
import hashlib
import json
import re

from .errors import SkillError, require

SOURCE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
VERSION = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def canonical(value):
    def check(item):
        if isinstance(item, dict):
            require(all(isinstance(k, str) for k in item), "JSON keys must be strings")
            for k, v in item.items():
                k.encode("utf-8")
                check(v)
        elif isinstance(item, list):
            for v in item:
                check(v)
        elif isinstance(item, str):
            item.encode("utf-8")
        else:
            require(item is None or type(item) in (bool, int, float), "Value is not JSON-compatible")
    try:
        check(value)
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SkillError("validation", f"Invalid finite UTF-8 JSON: {exc}") from exc


def byte_digest(content):
    return "sha256:" + hashlib.sha256(content).hexdigest()


def digest(value):
    return byte_digest(canonical(value))


def loads(content):
    def pairs(items):
        result = {}
        for k, v in items:
            k.encode("utf-8")
            require(k not in result, f"Duplicate JSON key: {k}")
            result[k] = v
        return result
    def invalid(value):
        raise SkillError("validation", f"Nonfinite JSON number: {value}")
    try:
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        result = json.loads(content, object_pairs_hook=pairs, parse_constant=invalid)
        canonical(result)
        return result
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise SkillError("validation", f"Invalid JSON: {exc}") from exc


def skill_name(name):
    require(isinstance(name, str) and len(name) <= 64 and NAME.fullmatch(name),
            "Skill name must be 1-64 lowercase letters/numbers with single internal hyphens")
    return name


def source_id(identifier):
    require(isinstance(identifier, str) and SOURCE.fullmatch(identifier), "Invalid source ID")
    return identifier


def reference(value):
    require(isinstance(value, str) and value.count("@") == 1, "Expected source/name@version-or-digest")
    scoped, version = value.split("@")
    require(scoped.count("/") == 1, "Release reference requires source/name")
    source, name = scoped.split("/")
    source_id(source)
    skill_name(name)
    require(VERSION.fullmatch(version) or DIGEST.fullmatch(version), "Invalid release version or digest")
    return source, name, version
