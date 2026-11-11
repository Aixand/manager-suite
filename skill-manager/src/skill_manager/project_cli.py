"""Collection/project commands over the public Manager and Project APIs."""

from .errors import require

COMMANDS = {
    "import",
    "connect",
    "connections",
    "update-connections",
    "disconnect",
    "apply-project-plan",
    "restore-connections",
    "project-operations",
    "recover-project",
    "save-project-copy",
}


def install(commands):
    collection = commands.add_parser(
        "import", help="Discover/select native skills from a local collection"
    )
    collection.add_argument("path")
    choices = collection.add_mutually_exclusive_group()
    choices.add_argument("--select", action="append", dest="selections")
    choices.add_argument("--all", action="store_true")
    collection.add_argument(
        "--expected-revision",
        help="Discovery revision guard; requires --select or --all",
    )
    collection.add_argument("--json", action="store_true")
    for name in sorted(COMMANDS - {"import"}):
        descriptions = {
            "connect": "Connect selected accepted skills to a project",
            "connections": "Inspect installed revisions and local edits",
            "update-connections": "Install newer accepted library content (no publisher fetch)",
            "disconnect": "Disconnect selected unchanged skills, retaining backups",
            "apply-project-plan": "Apply an unchanged saved preview plan",
            "restore-connections": "Restore exact content recorded in the project manifest",
            "project-operations": "List the latest 100 project operation receipts",
            "recover-project": "Explicitly resume or roll back an interrupted operation",
            "save-project-copy": "Save project edits as an independent managed variant",
        }
        parser = commands.add_parser(
            name, help=descriptions[name], description=descriptions[name]
        )
        parser.add_argument(
            "--project",
            default=".",
            help="Exact project directory (default: current directory)",
        )
        parser.add_argument("--json", action="store_true")
        if name in ("connect", "disconnect"):
            parser.add_argument("skills", nargs="+")
        if name == "update-connections":
            parser.add_argument(
                "skills", nargs="*", help="Selected connections; omit to update all"
            )
        if name == "connect":
            parser.add_argument(
                "--skills-dir",
                help="Relative skills directory; required for the first connection",
            )
        if name in ("connect", "restore-connections"):
            parser.add_argument("--adopt", action="store_true")
        if name in (
            "connect",
            "disconnect",
            "update-connections",
            "restore-connections",
        ):
            parser.add_argument("--preview", action="store_true")
        if name == "disconnect":
            parser.add_argument(
                "--forget",
                action="store_true",
                help="Drop ownership and leave project files in place",
            )
        if name == "apply-project-plan":
            parser.add_argument("--plan", required=True, help="Inline JSON or @FILE")
        if name == "recover-project":
            parser.add_argument("operation")
            parser.add_argument(
                "--action", choices=("resume", "rollback"), default="resume"
            )
        if name == "save-project-copy":
            parser.add_argument("skill")
            parser.add_argument("--as", required=True, dest="variant_id")
            parser.add_argument("--expected-revision", required=True)
            parser.add_argument("--expected-digest", required=True)


def dispatch(manager, args, input_json):
    name = args.command
    if name == "import":
        if args.selections is None and not args.all:
            require(
                args.expected_revision is None,
                "--expected-revision requires --select or --all",
            )
            return manager.discover_collection(args.path)
        return manager.import_collection(
            args.path,
            args.selections,
            all=args.all,
            expected_revision=args.expected_revision,
        )
    project = manager.project(args.project)
    if name == "connections":
        return project.connections()
    if name == "project-operations":
        return project.operations()
    if name == "apply-project-plan":
        return project.apply(input_json(args.plan))
    if name == "recover-project":
        return project.recover(args.operation, action=args.action)
    if name == "save-project-copy":
        return project.save_variant(
            args.skill,
            args.variant_id,
            expected_revision=args.expected_revision,
            expected_digest=args.expected_digest,
        )
    if name == "connect":
        plan = project.preview_connect(
            args.skills, skills_dir=args.skills_dir, adopt=args.adopt
        )
    elif name == "update-connections":
        plan = project.preview_update(args.skills or None)
    elif name == "disconnect":
        plan = project.preview_disconnect(args.skills, forget=args.forget)
    else:
        plan = project.preview_restore(adopt=args.adopt)
    return plan if args.preview else project.apply(plan)
