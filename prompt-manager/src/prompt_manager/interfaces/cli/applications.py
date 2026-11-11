"""Application command syntax and public-API dispatch."""

from .arguments import _argument_json, _revision


def install(sub):
    application = sub.add_parser("application", help="Manage logical application prompt selections")
    commands = application.add_subparsers(dest="application_command", required=True)
    registration = commands.add_parser("register")
    registration.add_argument("identifier")
    registration.add_argument("--prompts", required=True, help="JSON mapping or @FILE")
    registration.add_argument("--label")
    registration.add_argument("--fallback-policy", choices=["none", "packaged"], default="none")
    registration.add_argument("--expected-revision", default="none")
    commands.add_parser("list")
    inspection = commands.add_parser("show")
    inspection.add_argument("identifier")
    selection = commands.add_parser("set")
    selection.add_argument("identifier")
    selection.add_argument("logical_prompt")
    selection.add_argument("--target", required=True, help="JSON target or null; @FILE accepted")
    selection.add_argument("--expected-revision", required=True)
    removal = commands.add_parser("unregister")
    removal.add_argument("identifier")
    removal.add_argument("--expected-revision", required=True)
    for command in ("resolve", "render"):
        effective = commands.add_parser(command)
        effective.add_argument("identifier")
        effective.add_argument("logical_prompt")
        effective.add_argument("--fallback", help="Explicit packaged fallback JSON or @FILE")
        if command == "render":
            effective.add_argument("--inputs", help="JSON input object or @FILE; defaults to {}")
            effective.add_argument("--format", choices=["text", "messages"], default="text")
            effective.add_argument("--json", action="store_true")


def dispatch(manager, args):
    command = args.application_command
    if command == "register":
        return manager.register_application(
            args.identifier,
            prompts=_argument_json(args.prompts),
            label=args.label,
            fallback_policy=args.fallback_policy,
            expected_revision=_revision(args.expected_revision),
        )
    if command == "list":
        return manager.applications()
    if command == "show":
        return manager.application_descriptor(args.identifier)
    if command == "set":
        return manager.set_application_prompt(
            args.identifier,
            args.logical_prompt,
            _argument_json(args.target),
            expected_revision=_revision(args.expected_revision),
        )
    if command == "unregister":
        return manager.unregister_application(
            args.identifier,
            expected_revision=_revision(args.expected_revision),
        )
    fallback = _argument_json(args.fallback) if args.fallback else None
    if command == "resolve":
        return manager.resolve_application(args.identifier, args.logical_prompt, fallback=fallback)
    return manager.render_application(
        args.identifier,
        args.logical_prompt,
        _argument_json(args.inputs) if args.inputs else {},
        format=args.format,
        fallback=fallback,
    )
