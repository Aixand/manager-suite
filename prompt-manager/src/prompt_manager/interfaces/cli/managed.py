"""Owned prompt registration, editing, review, and history commands."""


def install(commands):
    group = commands.add_parser(
        "managed", help="Owned copies, publisher updates and reconciliation"
    )
    operations = group.add_subparsers(dest="managed_command", required=True)

    def command(name, *, identity=True, guard=False):
        parser = operations.add_parser(name)
        parser.add_argument("--json", action="store_true")
        if identity:
            parser.add_argument("publisher")
            parser.add_argument("identifier")
        if guard:
            parser.add_argument("--expected-revision", required=True)
        return parser

    create = command("create", identity=False)
    create.add_argument("name")
    create.add_argument("--content", default="")
    create.add_argument("--role", choices=["system", "user", "assistant"], default="user")
    local = command("import-local", identity=False)
    local.add_argument("path")
    local.add_argument("--package-id")
    register = command("register")
    register.add_argument("path")
    register.add_argument("--publisher-revision", type=int)
    register.add_argument("--version")
    register.add_argument("--prompt")
    register.add_argument("--inputs")
    register.add_argument("--format", choices=["text", "messages"], default="text")
    ensure = command("ensure")
    ensure.add_argument("path")
    ensure.add_argument("--publisher-revision", type=int)
    ensure.add_argument("--version")
    search = command("search", identity=False)
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--publisher", dest="publisher_id")
    search.add_argument("--tag", action="append", default=[], dest="tags")
    archives = search.add_mutually_exclusive_group()
    archives.add_argument("--archived", action="store_const", const=True, dest="archived")
    archives.add_argument("--include-archived", action="store_const", const=None, dest="archived")
    custom = search.add_mutually_exclusive_group()
    custom.add_argument("--customized", action="store_const", const=True, dest="customized")
    custom.add_argument("--unmodified", action="store_const", const=False, dest="customized")
    pending = search.add_mutually_exclusive_group()
    pending.add_argument(
        "--pending-review", action="store_const", const=True, dest="reconciliation_required"
    )
    pending.add_argument(
        "--no-pending-review", action="store_const", const=False, dest="reconciliation_required"
    )
    search.set_defaults(archived=False, customized=None, reconciliation_required=None)
    tags = command("set-tags", guard=True)
    values = tags.add_mutually_exclusive_group(required=True)
    values.add_argument("--tag", action="append", dest="tags")
    values.add_argument("--clear", action="store_true")
    command("refresh-metadata", guard=True)
    listing = command("list", identity=False)
    listing.add_argument("--include-archived", action="store_true")
    rename = command("rename", guard=True)
    labels = rename.add_mutually_exclusive_group(required=True)
    labels.add_argument("--label")
    labels.add_argument("--clear-label", action="store_true")
    command("archive", guard=True)
    command("unarchive", guard=True)
    duplicate = command("duplicate")
    duplicate.add_argument("target_id")
    duplicate.add_argument("--revision")
    duplicate.add_argument("--label")
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
    validate = command("validate")
    validate.add_argument("--files", help="Optional candidate changes as JSON or @FILE")
    validate.add_argument("--revision")
    compare = command("compare")
    compare.add_argument("before_revision")
    compare.add_argument("--after-revision")
    compare.add_argument("--prompt")
    compare.add_argument("--inputs")
    compare.add_argument("--format", choices=["text", "messages"], default="text")
    export = command("export")
    export.add_argument("destination")
    export.add_argument("--revision")
    render = command("render")
    render.add_argument("prompt")
    render.add_argument("--inputs")
    render.add_argument("--format", choices=["text", "messages"], default="text")
    render.add_argument("--revision")


def dispatch(manager, args, input_json):
    op = args.managed_command
    if op == "create":
        return manager.create_managed(args.name, content=args.content, role=args.role)
    if op == "import-local":
        return manager.import_local(args.path, package_id=args.package_id)
    if op == "search":
        return manager.search_managed(
            args.query,
            publisher_id=args.publisher_id,
            tags=args.tags,
            archived=args.archived,
            customized=args.customized,
            reconciliation_required=args.reconciliation_required,
        )
    if op == "list":
        return manager.managed_catalog(include_archived=args.include_archived)
    package = manager.package(args.publisher, args.identifier)
    if op == "set-tags":
        return package.set_tags(
            [] if args.clear else args.tags, expected_revision=args.expected_revision
        )
    if op == "refresh-metadata":
        return package.refresh_metadata(expected_revision=args.expected_revision)
    if op == "rename":
        return package.rename(
            None if args.clear_label else args.label, expected_revision=args.expected_revision
        )
    if op == "archive":
        return package.archive(expected_revision=args.expected_revision)
    if op == "unarchive":
        return package.unarchive(expected_revision=args.expected_revision)
    if op == "duplicate":
        return package.duplicate(args.target_id, revision=args.revision, label=args.label)
    if op == "register":
        return package.register(
            args.path,
            prompt_id=args.prompt,
            inputs=input_json(args.inputs) if args.inputs is not None else None,
            format=args.format,
            publisher_revision=args.publisher_revision,
            version=args.version,
        ).to_dict()
    if op == "ensure":
        return package.refresh(
            args.path,
            publisher_revision=args.publisher_revision,
            version=args.version,
        )
    if op == "status":
        return package.status(revision=args.revision)
    if op == "history":
        return package.history()
    if op == "save":
        files = input_json(args.files)
        return package.save(files, expected_revision=args.expected_revision)
    if op in {"preview", "apply"}:
        options = {
            "resolutions": input_json(args.resolutions) if args.resolutions else None,
            "take": args.take,
        }
        if op == "apply":
            return package.reconcile(expected_revision=args.expected_revision, **options)
        return package.preview_reconciliation(**options)
    if op == "rollback":
        return package.restore(args.revision, expected_revision=args.expected_revision)
    if op == "tracking":
        return package.set_tracking(args.enabled == "on", expected_revision=args.expected_revision)
    if op == "resolve":
        return package.resolve(revision=args.revision)
    if op == "release":
        return package.release(args.version, revision=args.revision)
    if op == "validate":
        return package.validate(
            input_json(args.files) if args.files is not None else None, revision=args.revision
        )
    if op == "compare":
        return package.compare(
            args.before_revision,
            args.after_revision,
            prompt_id=args.prompt,
            inputs=input_json(args.inputs) if args.inputs is not None else None,
            format=args.format,
        )
    if op == "export":
        return package.export(args.destination, revision=args.revision)
    return package.render(
        args.prompt,
        input_json(args.inputs) if args.inputs else {},
        format=args.format,
        revision=args.revision,
    )
