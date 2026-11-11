"""Strict JSON argument and input-file decoding."""
import json
from pathlib import Path

from ...errors import McpError
from ...live.service import json_copy


def json_input(value):
    def pairs(items):
        result = {}
        for key, child in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = child
        return result
    try:
        text = Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value
        parsed = json.loads(text, object_pairs_hook=pairs,
                            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
        return json_copy(parsed)
    except OSError as error:
        raise McpError("io", "Cannot read JSON input file") from error
    except (ValueError, UnicodeError) as error:
        raise McpError("validation", "Invalid finite UTF-8 JSON input (duplicate keys are rejected)") from error

