"""JSON interfaces for owned registrations; identical in both distributions."""

import base64

from .errors import require


def install(commands, *, skills=False):
    group = commands.add_parser(
        "managed", help="Owned copies, publisher updates and reconciliation"
    )
    operations = group.add_subparsers(dest="managed_command", required=True)
    from .source_cli import install as install_sources
    install_sources(operations)

    def command(name, *, identity=True, guard=False):
        parser = operations.add_parser(name)
        parser.add_argument("--json", action="store_true")
        if identity:
            parser.add_argument("publisher")
            parser.add_argument("identifier")
        if guard:
            parser.add_argument("--expected-revision", required=True)
        return parser

    ensure = command("ensure")
    ensure.add_argument("path")
    ensure.add_argument("--publisher-revision", type=int)
    ensure.add_argument("--version")
    command("list", identity=False)
    show = command("status")
    show.add_argument("--revision")
    command("history")
    save = command("save", guard=True)
    save.add_argument(
        "--files", required=True, help="JSON file changes or @FILE; null deletes a file"
    )
    for name in ("preview", "apply"):
        review = command(name, guard=name == "apply")
        review.add_argument("--resolutions", help="JSON conflict choices or @FILE")
        review.add_argument("--take", choices=["managed", "publisher"])
    rollback = command("rollback", guard=True)
    rollback.add_argument("revision")
    tracking = command("tracking", guard=True)
    tracking.add_argument("enabled", choices=["on", "off"])
    resolve = command("resolve")
    resolve.add_argument("--revision")
    release = command("release")
    release.add_argument("--version", required=True)
    release.add_argument("--revision")
    if skills:
        read = command("read")
        read.add_argument("file")
        read.add_argument("--revision")
        export = command("export")
        export.add_argument("destination")
        export.add_argument("--revision")
    else:
        render = command("render")
        render.add_argument("prompt")
        render.add_argument("--inputs")
        render.add_argument("--format", choices=["text", "messages"], default="text")
        render.add_argument("--revision")


def dispatch(manager, args, input_json, *, skills=False):
    from .source_cli import COMMANDS
    from .source_cli import dispatch as dispatch_source
    if args.managed_command in COMMANDS:
        return dispatch_source(manager, args, input_json)
    op = args.managed_command
    if op == "list":
        return manager.managed_catalog()
    publisher, identifier = args.publisher, args.identifier
    if op == "ensure":
        keyword = "skill_id" if skills else "package_id"
        return manager.ensure_registered(
            publisher,
            args.path,
            **{keyword: identifier},
            publisher_revision=args.publisher_revision,
            version=args.version,
        )
    if op == "status":
        return manager.managed_status(publisher, identifier, revision=args.revision)
    if op == "history":
        return manager.managed_history(publisher, identifier)
    if op == "save":
        files = input_json(args.files)
        if skills:
            require(isinstance(files, dict), "File changes must be an object")
            converted = {}
            for path, value in files.items():
                if value is None:
                    converted[path] = None
                    continue
                require(
                    isinstance(value, dict)
                    and set(value) <= {"content_b64", "executable"}
                    and isinstance(value.get("content_b64"), str),
                    "Skill files require content_b64",
                )
                try:
                    content = base64.b64decode(value["content_b64"], validate=True)
                except ValueError:
                    require(False, "Invalid file base64")
                converted[path] = {"content": content, "executable": value.get("executable", False)}
            files = converted
        return manager.save_managed(
            publisher, identifier, files, expected_revision=args.expected_revision
        )
    if op in {"preview", "apply"}:
        options = {
            "resolutions": input_json(args.resolutions) if args.resolutions else None,
            "take": args.take,
        }
        if op == "apply":
            return manager.reconcile(
                publisher, identifier, expected_revision=args.expected_revision, **options
            )
        return manager.preview_reconciliation(publisher, identifier, **options)
    if op == "rollback":
        return manager.rollback_managed(
            publisher, identifier, args.revision, expected_revision=args.expected_revision
        )
    if op == "tracking":
        return manager.set_managed_tracking(
            publisher, identifier, args.enabled == "on", expected_revision=args.expected_revision
        )
    if op == "resolve":
        return manager.resolve_managed(publisher, identifier, revision=args.revision)
    if op == "release":
        return manager.release_managed(publisher, identifier, args.version, revision=args.revision)
    if op == "read":
        content = manager.read_managed_file(
            publisher, identifier, args.file, revision=args.revision
        )
        return {"content_b64": base64.b64encode(content).decode("ascii")}
    if op == "export":
        return manager.export_managed(
            publisher, identifier, args.destination, revision=args.revision
        )
    return manager.render_managed(
        publisher,
        identifier,
        args.prompt,
        input_json(args.inputs) if args.inputs else {},
        format=args.format,
        revision=args.revision,
    )
