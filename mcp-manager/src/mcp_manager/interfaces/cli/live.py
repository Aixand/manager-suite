"""Explicit connector factory loading and runtime command dispatch."""
import importlib
from ...errors import McpError
from .input import json_input


def load_connector(spec, options):
    if not isinstance(options, dict):
        raise McpError("validation", "Connector options must be a JSON object")
    module, separator, attribute = spec.partition(":")
    if not separator or not module or not attribute or ":" in attribute:
        raise McpError("validation", "Connector must be MODULE:factory")
    try:
        factory = getattr(importlib.import_module(module), attribute)
        return factory(options)
    except Exception as error:
        raise McpError("validation", "Cannot load configured connector factory") from error


async def runtime_execute(manager, args):
    connector = load_connector(args.connector, json_input(args.options))
    target = json_input(args.target)
    if args.runtime_action == "capabilities":
        return await manager.runtime_capabilities(connector, target)
    if args.runtime_action == "status":
        return await manager.runtime_status(connector, target, scope=args.scope, server_name=args.server_name)
    return await manager.reconnect(connector, target, scope=args.scope, server_name=args.server_name,
        operation_id=args.operation_id, expected_selection_digest=args.expected_selection_digest)

