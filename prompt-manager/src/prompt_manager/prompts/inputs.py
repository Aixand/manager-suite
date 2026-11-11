"""Typed prompt inputs, defaults, and binding."""

import math

from ..codec import canonical, clone
from ..errors import require
from .definitions import VARIABLE, fields, name

TYPES = {"string", "integer", "number", "boolean", "object", "array", "null"}


def has_type(value, kind):
    return {
        "string": lambda: isinstance(value, str),
        "integer": lambda: type(value) is int,
        "number": lambda: (
            type(value) in (int, float) and (type(value) is int or math.isfinite(value))
        ),
        "boolean": lambda: type(value) is bool,
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "null": lambda: value is None,
    }[kind]()


def check_value(value, spec, where):
    require(has_type(value, spec["type"]), f"{where}: expected {spec['type']}", "inputs")
    if "enum" in spec:
        require(
            any(canonical(value) == canonical(v) for v in spec["enum"]),
            f"{where}: value is outside the declared enum",
            "inputs",
        )


def input_specs(specs, where):
    require(isinstance(specs, dict), f"{where}: expected an input mapping")
    for key, spec in specs.items():
        name(key, where, VARIABLE)
        fields(spec, {"type", "default", "enum", "description"}, {"type"}, f"{where}.{key}")
        require(
            isinstance(spec["type"], str) and spec["type"] in TYPES,
            f"{where}.{key}: unsupported type",
        )
        if "description" in spec:
            require(isinstance(spec["description"], str), f"{where}.{key}: invalid description")
        if "enum" in spec:
            require(isinstance(spec["enum"], list) and spec["enum"], f"{where}.{key}: empty enum")
            for value in spec["enum"]:
                require(has_type(value, spec["type"]), f"{where}.{key}: enum type mismatch")
        if "default" in spec:
            check_value(spec["default"], spec, f"{where}.{key}.default")


def bind(specs, supplied, where):
    require(isinstance(supplied, dict), f"{where}: inputs must be an object", "inputs")
    require(
        not (set(supplied) - set(specs)),
        f"{where}: undeclared inputs {sorted(set(supplied) - set(specs))}",
        "inputs",
    )
    values = {}
    for key, spec in specs.items():
        require(key in supplied or "default" in spec, f"{where}: missing input {key}", "inputs")
        value = supplied[key] if key in supplied else spec["default"]
        check_value(value, spec, f"{where}.{key}")
        values[key] = clone(value)
    return values
