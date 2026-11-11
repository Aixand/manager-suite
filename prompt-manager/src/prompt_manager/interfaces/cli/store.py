"""Store inspection and validated backup/restore commands."""


def install(commands):
    group = commands.add_parser("store", help="Store inspection, backup, and restore")
    operations = group.add_subparsers(dest="store_command", required=True)
    check = operations.add_parser("check")
    check.add_argument(
        "--no-external", action="store_true", help="Skip external draft availability"
    )
    backup = operations.add_parser("backup", help="Back up the selected home to a ZIP archive")
    backup.add_argument("destination")
    restore = operations.add_parser("restore", help="Restore an archive into an unused home path")
    restore.add_argument("source")


def dispatch(manager, args):
    if args.store_command == "check":
        return manager.check_store(include_external=not args.no_external)
    if args.store_command == "backup":
        return manager.backup_store(args.destination)
    return manager.restore_store(args.source)
