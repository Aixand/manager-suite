"""Installed CLI entry point, output formatting, and diagnostics."""

import json
import sys

from ...errors import PromptError
from ...manager import Manager
from . import applications, library, managed, sources, store
from .arguments import _argument_json
from .parser import parser


def _json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))


def main(argv=None):
    args = parser().parse_args(argv)
    manager = Manager(args.home, packs=args.packs)
    try:
        if args.command == "store":
            result = store.dispatch(manager, args)
        elif args.command == "managed":
            result = managed.dispatch(manager, args, _argument_json)
        elif args.command == "application":
            result = applications.dispatch(manager, args)
        elif args.command in {"register-source", "unregister"}:
            result = sources.dispatch(manager, args)
        else:
            result = library.dispatch(manager, args)
        is_render = args.command in {"render", "preview"} or (
            args.command == "application" and args.application_command == "render"
        )
        if is_render and not args.json:
            if args.format == "text":
                sys.stdout.write(result["output"])
            else:
                _json(result["output"])
        elif args.command == "diff" and not args.json:
            sys.stdout.write(result["definition_diff"])
            if result["render_diff"] is not None:
                sys.stdout.write(result["render_diff"])
        else:
            _json(result)
        return (
            1
            if args.command == "store" and args.store_command == "check" and not result["ok"]
            else 0
        )
    except BrokenPipeError:
        return 1
    except (PromptError, OSError) as exc:
        error = exc if isinstance(exc, PromptError) else PromptError("io", str(exc))
        print(json.dumps({"error": error.to_dict()}, ensure_ascii=False), file=sys.stderr)
        return 1
