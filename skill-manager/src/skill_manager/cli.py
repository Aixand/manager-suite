"""CLI with structured results, binary-safe file inputs, and explicit guards."""
import argparse
import base64
import binascii
import json
import sys

from . import Manager, SkillError, __version__
from .codec import loads
from .errors import require
from .fs import read_path
from . import managed_cli
from . import project_cli


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise SkillError("validation", f"Invalid command arguments: {message}")


def revision(value):
    return None if value == "none" else value


def input_json(value):
    return loads(read_path(value[1:]) if value.startswith("@") else value)


def parser():
    root = ArgumentParser(prog="skill-manager", description="Manage local native SKILL.md packages")
    root.add_argument("--home")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    managed_cli.install(commands, skills=True)
    project_cli.install(commands)

    def command(parent, name, **options):
        item = parent.add_parser(name, **options)
        item.add_argument("--json", action="store_true", help="Emit strict JSON (also used by default)")
        return item

    source = command(commands, "source")
    sources = source.add_subparsers(dest="source_command", required=True)
    register = command(sources, "register")
    register.add_argument("id")
    register.add_argument("paths", nargs="+")
    register.add_argument("--writable", action="store_true")
    register.add_argument("--label")
    register.add_argument("--owner", help="JSON metadata or @PATH")
    register.add_argument("--expected-revision", type=revision)
    command(sources, "list")
    show = command(sources, "show")
    show.add_argument("id")
    remove = command(sources, "unregister")
    remove.add_argument("id")
    remove.add_argument("--expected-revision", required=True, type=revision)
    command(commands, "catalog")
    show = command(commands, "show")
    show.add_argument("entry")
    show.add_argument("--file", help="Read one file as content_b64 without decoding its bytes")
    draft = command(commands, "draft")
    drafts = draft.add_subparsers(dest="draft_command", required=True)
    create = command(drafts, "create")
    create.add_argument("source")
    create.add_argument("name")
    create.add_argument("--files", required=True)
    create.add_argument("--path")
    save = command(drafts, "save")
    save.add_argument("entry")
    save.add_argument("relative")
    save.add_argument("--file", required=True)
    save.add_argument("--expected-digest", required=True)
    save.add_argument("--executable", action="store_true")
    validate = command(commands, "validate")
    validate.add_argument("entry")
    release = command(commands, "release")
    release.add_argument("entry")
    release.add_argument("--version", required=True)
    release.add_argument("--expected-digest", required=True)
    releases = command(commands, "releases", help="List retained immutable versions")
    releases.add_argument("--source")
    releases.add_argument("--name")
    resolve = command(commands, "resolve")
    resolve.add_argument("reference")
    diff = command(commands, "diff")
    diff.add_argument("before")
    diff.add_argument("after")
    export = command(commands, "export")
    export.add_argument("reference")
    export.add_argument("destination")
    return root


def dispatch(args):
    manager = Manager(args.home)
    if args.command in project_cli.COMMANDS:
        return project_cli.dispatch(manager, args, input_json)
    if args.command == "managed":
        return managed_cli.dispatch(manager, args, input_json, skills=True)
    if args.command == "source":
        if args.source_command == "register":
            return manager.register_source(args.id, args.paths, label=args.label, writable=args.writable,
                                           owner=input_json(args.owner) if args.owner else None,
                                           expected_revision=args.expected_revision)
        if args.source_command == "list":
            return {"schema": "skill-manager/sources-1", "sources": manager.sources()}
        if args.source_command == "show":
            return manager.source(args.id)
        return manager.unregister_source(args.id, expected_revision=args.expected_revision)
    if args.command == "catalog":
        return manager.catalog()
    if args.command == "show":
        if args.file:
            return {"schema": "skill-manager/file-1", "entry_id": args.entry, "path": args.file,
                    "content_b64": base64.b64encode(manager.read_file(args.entry, args.file)).decode("ascii")}
        return manager.show(args.entry)
    if args.command == "draft":
        if args.draft_command == "create":
            specs = input_json(args.files)
            require(isinstance(specs, dict), "--files must be a mapping")
            files = {}
            for path, spec in specs.items():
                require(isinstance(spec, dict) and set(spec) <= {"content_b64", "executable"}
                        and isinstance(spec.get("content_b64"), str), "File inputs require content_b64")
                try:
                    content = base64.b64decode(spec["content_b64"], validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise SkillError("validation", f"Invalid base64 for {path}") from exc
                files[path] = {"content": content, "executable": spec.get("executable", False)}
            return manager.create_draft(args.source, args.name, files, path=args.path)
        return manager.save_file(args.entry, args.relative, read_path(args.file), executable=args.executable,
                                 expected_digest=args.expected_digest)
    if args.command == "validate":
        return manager.validate(args.entry)
    if args.command == "release":
        return manager.release(args.entry, args.version, expected_digest=args.expected_digest)
    if args.command == "releases":
        return {"schema": "skill-manager/releases-1", "releases": manager.releases(args.source, args.name)}
    if args.command == "resolve":
        return manager.resolve(args.reference)
    if args.command == "diff":
        return manager.diff(args.before, args.after)
    return manager.export(args.reference, args.destination)


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        result = dispatch(args)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False))
        return 2 if args.command == "validate" and not result["valid"] else 0
    except (SkillError, OSError) as exc:
        error = exc if isinstance(exc, SkillError) else SkillError("io", str(exc))
        print(json.dumps({"schema": "skill-manager/error-1", "error": error.to_dict()},
                         sort_keys=True, ensure_ascii=False, allow_nan=False), file=sys.stderr)
        if error.uncertain or error.code == "uncertain":
            return 4
        return {"validation": 2, "conflict": 2, "not_found": 2, "unsupported": 3}.get(error.code, 1)
