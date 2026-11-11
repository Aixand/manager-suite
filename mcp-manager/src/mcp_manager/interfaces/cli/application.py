"""CLI entry point, structured errors and exit codes."""
import json
import sys

from ...errors import McpError
from .parser import Parser, parser
from .input import json_input
from .live import load_connector, runtime_execute
from .dispatch import execute


def exit_code(result):
    status = result.get("status") if isinstance(result, dict) else None
    return {"failed": 1, "interrupted": 1, "unsupported": 3, "uncertain": 4}.get(status, 0)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = None
    try:
        args = parser().parse_args(argv)
        result = execute(args)
        code = exit_code(result)
    except McpError as error:
        result = {"schema": "mcp-manager/error-1", "error": error.to_dict()}
        code = 4 if error.uncertain else 2 if error.code in ("validation", "conflict", "not_found") else 3 if error.code == "unsupported" else 1
    except (OSError, ValueError, TypeError) as error:
        result = {"schema": "mcp-manager/error-1", "error": McpError(
            "io" if isinstance(error, OSError) else "validation", "Invalid input or unavailable local path").to_dict()}
        code = 1 if isinstance(error, OSError) else 2
    except KeyboardInterrupt:
        operation_id = getattr(args, "operation_id", None)
        result = {"schema": "mcp-manager/error-1", "error": McpError("uncertain",
            "Client interrupted; inspect the retained operation before taking further action",
            uncertain=True, operation_id=operation_id,
            recovery={"operation_id": operation_id, "home": getattr(args, "home", None)}).to_dict()}
        code = 4
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=None if "--json" in argv else 2))
    return code
