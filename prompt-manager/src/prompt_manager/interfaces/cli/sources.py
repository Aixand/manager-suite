"""Source command syntax and independent source-CLI adapter."""

from ...storage.records import UNSET
from .arguments import _argument_json, _revision


def install(sub):
    register = sub.add_parser("register-source", help="Register durable draft pack directories")
    register.add_argument("identifier")
    register.add_argument("paths", nargs="+")
    register.add_argument("--label")
    register.add_argument("--owner", help="JSON ownership metadata or @FILE")
    register.add_argument("--read-only", action="store_true")
    register.add_argument("--expected-revision", default=UNSET)
    unregister = sub.add_parser("unregister", help="Remove a discovery registration")
    unregister.add_argument("kind", choices=["sources"])
    unregister.add_argument("identifier")


def dispatch(manager, args):
    if args.command == "register-source":
        return manager.register_source(
            args.identifier,
            args.paths,
            label=args.label,
            owner=_argument_json(args.owner) if args.owner else None,
            writable=not args.read_only,
            expected_revision=UNSET
            if args.expected_revision is UNSET
            else _revision(args.expected_revision),
        )
    return manager.unregister(args.kind, args.identifier)
