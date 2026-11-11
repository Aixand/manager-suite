"""Shared identifier validation, independent of profile and runtime services."""
import re

from agent_manager.errors import Error


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise Error(f"Invalid name: {value!r}")
    return value
