"""Single-pass interpolation of declared inputs."""

import re

from ..codec import canonical
from ..errors import require

TOKEN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\|\s*(json)\s*)?\}\}")


def check_template(text, specs, where):
    require(isinstance(text, str), f"{where}: template must be text")
    remainder = TOKEN.sub("", text)
    require("{{" not in remainder, f"{where}: malformed template expression")
    for match in TOKEN.finditer(text):
        key, formatter = match.groups()
        require(key in specs, f"{where}: undeclared template input {key}")
        require(
            formatter == "json" or specs[key]["type"] == "string",
            f"{where}: use the json formatter for non-string input {key}",
        )


def render_template(text, values):
    def replace(match):
        key, formatter = match.groups()
        return canonical(values[key]).decode() if formatter else values[key]

    return TOKEN.sub(replace, text)
