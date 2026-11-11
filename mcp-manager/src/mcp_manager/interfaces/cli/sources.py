"""MCP source import, tracking and installation commands."""

from __future__ import annotations

import time

from ...errors import McpError as SourceError

COMMANDS = {
    "create",
    "discover",
    "import-github",
    "import-discovery",
    "import-local",
    "check-updates",
    "source-status",
    "source-settings",
    "source-jobs",
    "source-job",
    "source-cancel",
    "switch-source",
    "install",
}


def install(commands):
    for name in sorted(COMMANDS):
        p = commands.add_parser(name)
        p.add_argument("--json", action="store_true")
        if name in {
            "create",
            "discover",
            "import-github",
            "import-discovery",
            "import-local",
            "check-updates",
            "switch-source",
            "install",
        }:
            p.add_argument("--background", action="store_true")
            p.add_argument(
                "--key", help="Retained request UUID; retries inspect the original job"
            )
        if name in {"discover", "import-github"}:
            p.add_argument("url")
            p.add_argument("--ref")
            p.add_argument("--path")
            tracking = p.add_mutually_exclusive_group()
            tracking.add_argument("--track", dest="track", action="store_true")
            tracking.add_argument("--pin", dest="track", action="store_false")
            p.set_defaults(track=None)
        if name in {"import-github", "import-discovery"}:
            p.add_argument("--select", action="append", dest="selections")
            p.add_argument(
                "--role", choices=("system", "user", "assistant"), default="system"
            )
        if name == "import-discovery":
            p.add_argument("discovery_id")
        if name == "import-local":
            p.add_argument("path")
        if name in {
            "check-updates",
            "source-status",
            "source-settings",
            "switch-source",
            "install",
        }:
            p.add_argument("publisher")
            p.add_argument("identifier")
        if name in {"source-settings", "switch-source", "install"}:
            p.add_argument("--expected-revision", required=True)
        if name == "check-updates":
            p.add_argument("--if-due", action="store_true")
        if name == "source-settings":
            p.add_argument("--label")
            p.add_argument("--interval-hours", type=int)
            tracking = p.add_mutually_exclusive_group()
            tracking.add_argument("--pin", dest="pinned", action="store_true")
            tracking.add_argument("--track", dest="pinned", action="store_false")
            p.set_defaults(pinned=None)
        if name in {"source-job", "source-cancel"}:
            p.add_argument("job_id")
        if name == "install":
            p.add_argument(
                "--candidate",
                choices=("active", "publisher", "reconciled"),
                default="active",
            )
            p.add_argument(
                "--resolutions", help="Reviewed conflict choices JSON or @FILE"
            )
            p.add_argument("--take", choices=("managed", "publisher"))
        if name == "switch-source":
            p.add_argument("--ref", required=True)
            p.add_argument("--track", action="store_true", default=None)
        if name == "create":
            p.add_argument("name")
            p.add_argument("--content", default="", help="Text or @FILE")
            p.add_argument("--description", default="")
            p.add_argument(
                "--role", choices=("system", "user", "assistant"), default="system"
            )
            p.add_argument("--definition", help="MCP definition JSON or @FILE")


def wait(manager, row):
    try:
        while row["status"] in ("queued", "running"):
            time.sleep(0.1)
            row = manager.source_job(row["id"])
    except KeyboardInterrupt:
        raise SourceError(
            "uncertain", f"Stopped waiting; inspect source-job {row['id']}"
        ) from None
    if row["status"] in ("failed", "interrupted", "cancelled"):
        error = row.get("error") or {"code": row["status"], "message": row["status"]}
        raise SourceError(
            error["code"], f"{error['message']}; inspect source-job {row['id']}"
        )
    return row


def dispatch(manager, args, json_input):
    name = args.managed_command
    if name == "source-jobs":
        return manager.source_jobs()
    if name == "source-job":
        return manager.source_job(args.job_id)
    if name == "source-cancel":
        return manager.cancel_source_job(args.job_id)
    if name == "source-status":
        return manager.source_status(args.publisher, args.identifier)
    if name == "source-settings":
        return manager.source_settings(
            args.publisher,
            args.identifier,
            expected_revision=args.expected_revision,
            label=args.label,
            interval_hours=args.interval_hours,
            pinned=args.pinned,
        )
    if name in ("discover", "import-github"):
        operation = name
        request = {
            "url": args.url,
            "ref": args.ref,
            "path": args.path,
            "track": args.track,
        }
        if name == "import-github":
            request.update(selections=args.selections, role=args.role)
    elif name == "import-discovery":
        operation = "import"
        request = {
            "discovery_id": args.discovery_id,
            "selections": args.selections,
            "role": args.role,
        }
    elif name == "import-local":
        operation, request = "import-local", {"path": args.path}
    elif name == "check-updates":
        operation, request = (
            "check",
            {
                "publisher": args.publisher,
                "identifier": args.identifier,
                "force": not args.if_due,
            },
        )
    elif name == "switch-source":
        operation, request = (
            "switch-source",
            {
                "publisher": args.publisher,
                "identifier": args.identifier,
                "expected_revision": args.expected_revision,
                "ref": args.ref,
                "track": args.track,
            },
        )
    elif name == "install":
        operation, request = (
            "install",
            {
                "publisher": args.publisher,
                "identifier": args.identifier,
                "expected_revision": args.expected_revision,
                "candidate": args.candidate,
                "resolutions": json_input(args.resolutions)
                if args.resolutions
                else None,
                "take": args.take,
            },
        )
    else:
        from pathlib import Path

        content = (
            Path(args.content[1:]).read_text(encoding="utf-8")
            if args.content.startswith("@")
            else args.content
        )
        operation, request = (
            "create",
            {
                "name": args.name,
                "content": content,
                "description": args.description,
                "role": args.role,
            },
        )
        if args.definition:
            request["definition"] = json_input(args.definition)
    row = manager.start_source_job(operation, request, key=args.key)
    return row if args.background else wait(manager, row)
