"""Map parsed commands to public SDK operations."""
import asyncio
import time

from ...manager import Manager
from ...library.definitions import AUTO
from ...errors import McpError
from .input import json_input
from .live import runtime_execute


def execute(args):
    manager = Manager(args.home)
    action = args.command
    if action == "managed":
        from .managed import dispatch
        return dispatch(manager, args, json_input)
    if action in ("catalog", "list"):
        return manager.catalog()
    if action == "show":
        return manager.show(args.name)
    if action == "add":
        return manager.add(args.name, json_input(args.definition))
    if action == "configure":
        return manager.configure(args.name, json_input(args.definition),
                                 expected_revision=None if args.expected_revision == "none" else args.expected_revision)
    if action == "resolve":
        project = None if args.no_project else args.project or AUTO
        return manager.resolve(json_input(args.servers), args.cwd, project=project, strict=args.strict, check=args.check)
    if action in ("prepare", "refresh"):
        settings = json_input(args.settings) if args.settings else {}
        if not isinstance(settings, dict):
            raise McpError("validation", "Preparation settings must be an object")
        settings.update({key: getattr(args, key) for key in ("resolve", "install_model", "model_dir", "usage_config", "resources")
                         if getattr(args, key) is not None})
        row = manager.prepare(args.tools, args.project, refresh=action == "refresh", settings=settings, key=args.key)
        if not args.background:
            try:
                while row["status"] in ("queued", "running"):
                    time.sleep(.1)
                    row = manager.job(row["id"])
            except KeyboardInterrupt:
                raise McpError("uncertain", "Client stopped waiting; inspect the retained job; it was not cancelled or replayed",
                    uncertain=True, operation_id=row["id"], recovery={"home": str(manager.home), "job_id": row["id"]}) from None
        return row
    if action == "status":
        return manager.status(args.project, servers=json_input(args.servers) if args.servers else None, check=args.check)
    if action == "job":
        return manager.job(args.job_id)
    if action == "jobs":
        return manager.jobs(args.project)
    if args.runtime_action == "operation":
        return manager.runtime_operation(args.operation_id)
    return asyncio.run(runtime_execute(manager, args))

