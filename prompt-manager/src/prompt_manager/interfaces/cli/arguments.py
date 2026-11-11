"""JSON command arguments and revision-token conversion."""

from ...codec import parse
from ...storage.files import read_json


def _argument_json(value):
    return read_json(value[1:]) if value.startswith("@") else parse(value, "CLI argument")


def _revision(value):
    return None if value == "none" else value
