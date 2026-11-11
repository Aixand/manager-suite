"""CLI for owned MCP configuration; no backend is needed for registration."""


def install(commands):
    group = commands.add_parser("managed", help="Owned definitions and publisher reconciliation")
    actions = group.add_subparsers(dest="managed_command", required=True)
    from .sources import install as install_sources
    install_sources(actions)
    for action in ("ensure", "list", "status", "resolve", "save", "preview", "apply", "history", "rollback", "tracking", "adopt", "prepare", "refresh", "inspect"):
        parser = actions.add_parser(action)
        parser.add_argument("--json", action="store_true")
        if action != "list":
            parser.add_argument("publisher")
            parser.add_argument("identifier")
        if action in ("ensure", "save", "adopt"):
            parser.add_argument("--definition", required=True, help="JSON definition or @PATH")
        if action in ("ensure", "adopt"):
            parser.add_argument("--publisher-revision", type=int)
            parser.add_argument("--version")
        if action == "adopt":
            parser.add_argument("--legacy-name", required=True)
        if action in ("save", "apply", "rollback", "tracking"):
            parser.add_argument("--expected-revision", required=True)
        if action in ("status", "resolve", "prepare", "refresh", "inspect"):
            parser.add_argument("--revision")
        if action in ("preview", "apply"):
            parser.add_argument("--resolutions")
            parser.add_argument("--take", choices=("managed", "publisher"))
        if action == "rollback":
            parser.add_argument("revision")
        if action == "tracking":
            parser.add_argument("enabled", choices=("on", "off"))
        if action == "inspect":
            parser.add_argument("--cwd", required=True)
            parser.add_argument("--project")
        if action in ("prepare", "refresh"):
            parser.add_argument("--project", required=True)
            parser.add_argument("--key")
            parser.add_argument("--settings")
            parser.add_argument("--background", action="store_true")


def dispatch(manager, args, json_input):
    from .sources import COMMANDS, dispatch as dispatch_source
    if args.managed_command in COMMANDS:
        return dispatch_source(manager, args, json_input)
    action = args.managed_command
    if action == "list":
        return manager.managed_catalog()
    publisher, identifier = args.publisher, args.identifier
    if action in ("ensure", "adopt"):
        options = dict(publisher_revision=args.publisher_revision, version=args.version)
        value = json_input(args.definition)
        if action == "adopt":
            legacy = manager.show(args.legacy_name)
            definition = {key: value for key, value in legacy.items() if key not in {"name", "revision", "installed"}}
            return manager.adopt_legacy(publisher, identifier, definition, value, **options)
        return manager.ensure_registered(publisher, value, server_id=identifier, **options)
    if action == "status":
        return manager.managed_status(publisher, identifier, revision=args.revision)
    if action == "inspect":
        from ...library.definitions import AUTO
        return manager.inspect_managed(publisher, identifier, args.cwd, revision=args.revision, project=args.project or AUTO)
    if action == "resolve":
        return manager.resolve_managed(publisher, identifier, revision=args.revision)
    if action == "save":
        return manager.save_managed(publisher, identifier, json_input(args.definition), expected_revision=args.expected_revision)
    if action in ("preview", "apply"):
        options = dict(resolutions=json_input(args.resolutions) if args.resolutions else None, take=args.take)
        if action == "apply":
            return manager.reconcile(publisher, identifier, expected_revision=args.expected_revision, **options)
        return manager.preview_reconciliation(publisher, identifier, **options)
    if action == "history":
        return manager.managed_history(publisher, identifier)
    if action == "rollback":
        return manager.rollback_managed(publisher, identifier, args.revision, expected_revision=args.expected_revision)
    if action == "tracking":
        return manager.set_managed_tracking(publisher, identifier, args.enabled == "on", expected_revision=args.expected_revision)
    row = manager.prepare_managed(publisher, identifier, args.project, revision=args.revision,
        refresh=action == "refresh", settings=json_input(args.settings) if args.settings else None, key=args.key)
    if not args.background:
        import time
        from ...errors import McpError
        try:
            while row["status"] in ("queued", "running"):
                time.sleep(.1)
                row = manager.job(row["id"])
        except KeyboardInterrupt:
            raise McpError("uncertain", "Stopped waiting; inspect the retained preparation job", uncertain=True,
                operation_id=row["id"], recovery={"job_id": row["id"]}) from None
    return row
