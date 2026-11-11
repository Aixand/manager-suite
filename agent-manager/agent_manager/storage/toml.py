"""Loss-checked TOML rewriting shared by settings and reset operations."""
from datetime import date, datetime, time
import json
import math
import re
import tomllib
from agent_manager.errors import Error

_BARE_TOML_KEY = re.compile(r"[A-Za-z0-9_-]+$")


def _toml_key(value):
    value = str(value)
    return value if _BARE_TOML_KEY.fullmatch(value) else json.dumps(value, ensure_ascii=False)


def _toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "-inf" if value < 0 else "inf"
        return repr(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(
            f"{_toml_key(key)} = {_toml_value(item)}" for key, item in value.items()
        ) + " }"
    raise Error("Provider configuration contains a value that cannot be restored safely")


def _toml_document(document):
    lines = []

    def table(path, value):
        if lines and lines[-1] != "":
            lines.append("")
        lines.append("[" + ".".join(map(_toml_key, path)) + "]")
        for key, item in value.items():
            if not isinstance(item, dict):
                lines.append(f"{_toml_key(key)} = {_toml_value(item)}")
        for key, item in value.items():
            if isinstance(item, dict):
                table((*path, key), item)

    for key, value in document.items():
        if not isinstance(value, dict):
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    for key, value in document.items():
        if isinstance(value, dict):
            table((key,), value)
    payload = (("\n".join(lines).rstrip() + "\n") if lines else "").encode()
    try:
        restored = tomllib.loads(payload.decode())
    except tomllib.TOMLDecodeError as error:
        raise Error("Provider configuration could not be rewritten safely") from error
    if restored != document:
        raise Error("Provider configuration rewrite did not preserve unrelated settings")
    return payload


def _toml_header_path(line):
    value = line.strip()
    if not value.startswith("[") or value.startswith("[["):
        return None
    marker = "agent_manager_reset_marker_7c8c42f4"
    try:
        document = tomllib.loads(value + "\n" + marker + " = true\n")
    except tomllib.TOMLDecodeError:
        return None

    def find(current, path=()):
        if isinstance(current, dict):
            if current.get(marker) is True:
                return path
            for key, item in current.items():
                found = find(item, (*path, key))
                if found is not None:
                    return found
        return None

    return find(document)


