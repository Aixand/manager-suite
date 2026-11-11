"""Library command syntax and public-API dispatch."""

from ...storage.files import read_json


def install(sub):
    sub.add_parser("list", help="List draft packs and local releases")
    catalog = sub.add_parser("catalog", help="Inspect registered sources and content revisions")
    catalog.add_argument("--include-archived", action="store_true")
    show = sub.add_parser("show", help="Inspect content, inputs, and resolved identities")
    show.add_argument("source")
    show.add_argument("--prompt")
    validate = sub.add_parser("validate", help="Validate a pack and run its rendering examples")
    validate.add_argument("source")
    render = sub.add_parser(
        "render", aliases=["preview"], help="Render a draft or immutable release"
    )
    render.add_argument("source")
    render.add_argument("--prompt")
    render.add_argument("--inputs", help="JSON input object; defaults to {}")
    render.add_argument("--format", choices=["text", "messages"], default="text")
    render.add_argument(
        "--json", action="store_true", help="Include provenance and the section explanation"
    )
    release = sub.add_parser("release", help="Publish a validated, immutable local snapshot")
    release.add_argument("source")
    release.add_argument("--version", required=True)
    diff = sub.add_parser("diff", help="Compare pack snapshots and optional rendered output")
    diff.add_argument("before")
    diff.add_argument("after")
    diff.add_argument("--prompt")
    diff.add_argument("--inputs", help="Use the same JSON inputs for both renderings")
    diff.add_argument("--format", choices=["text", "messages"], default="text")
    diff.add_argument("--json", action="store_true")


def dispatch(manager, args):
    if args.command == "list":
        return manager.list()
    if args.command == "catalog":
        return manager.catalog(include_archived=args.include_archived)
    if args.command == "show":
        return manager.show(args.source, prompt=args.prompt)
    if args.command == "validate":
        return manager.validate(args.source)
    if args.command == "release":
        return manager.release(args.source, args.version)
    if args.command in {"render", "preview"}:
        return manager.render(
            args.source,
            read_json(args.inputs) if args.inputs else {},
            prompt=args.prompt,
            format=args.format,
        )
    return manager.diff(
        args.before,
        args.after,
        prompt=args.prompt,
        format=args.format,
        inputs=read_json(args.inputs) if args.inputs else None,
    )
