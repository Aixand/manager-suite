"""Source metadata records; encoding is part of the compatibility contract."""
import json
from pathlib import Path

from ..errors import McpError
from .files import atomic

def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (ValueError, OSError) as error:
        raise McpError("io", f"Cannot read MCP metadata: {path}") from error


def write(path, value):
    atomic(Path(path), (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())
