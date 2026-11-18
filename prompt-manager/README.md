# Prompt Manager

Own, edit, compose, render, and version prompts with a standalone Python library
and CLI. Python 3.11+ is required. Installed runtime operations use only the
standard library and need no model, service, or network connection.

## Install the CLI

See [getting started](docs/getting-started.md#install-the-cli) for the hosted beta
installer, source installation, Windows setup, and a first owned prompt.
Python API consumers install the package in their application's environment.

## Install and create

```python
from prompt_manager import Manager

manager = Manager(home="/absolute/path/to/store", packs=[])
manager.create_managed("review", content="Review carefully.", role="system")
result = manager.package("local", "review").render("review", format="messages")
```

Creation is create-only. [Edit accepted content](docs/owned-prompts.md) with an
observed state revision. [Run a complete temporary-store example](docs/examples/owned-workflow.md).

## Guides and contracts

Start at the [documentation index](docs/README.md). Choose a task:

- [Register publishers and reconcile updates](docs/owned-prompts.md).
- [Import packs or keep file-backed drafts](docs/imports.md).
- [Compose templates and capture releases](docs/rendering.md).
- [Bind logical application selections](docs/integrations.md).
- [Organize the library](docs/library-management.md) and [search tags](docs/search.md).
- [Inspect storage](docs/storage-reliability.md) and [back up or restore](docs/backup-restore.md).
- [Understand ownership](docs/architecture-guide.md), [use the Python SDK](docs/sdk-reference.md),
  or [adapt a native package lifecycle](docs/native-packages.md).

The [pack format](docs/FORMAT.md), [managed contract](docs/MANAGED.md), and
[SDK inventory](docs/SDK_INVENTORY.md) remain source references. Model execution,
remote discovery, background jobs, and application startup belong to consumers.

## Development

Use the [local development guide](docs/development.md) for package setup,
checks, tests, and builds. Suite software releases are separate from local prompt
content releases. Shared [participation](../CONTRIBUTING.md), [support](../SUPPORT.md),
and [security](../SECURITY.md) policies apply.

## Owned imports and publisher updates

[Import owned content](docs/imports.md) and [review publishers](docs/owned-prompts.md).

## Packs, templates, and releases

[Compose, render, validate, and release packs](docs/rendering.md).

## External drafts and logical selections

[Register drafts](docs/imports.md) and [bind logical selections](docs/integrations.md).

## Storage and errors

[Store selection and CLI results](docs/getting-started.md) and [SDK errors](docs/sdk-reference.md).

## Standalone boundary and compatibility

[Integration ownership and compatibility](docs/integrations.md).

## Store inspection

[Inspect storage without repair or deletion](docs/storage-reliability.md).

## Full store backup and restore

[Capture and restore a validated full-store archive](docs/backup-restore.md).

## Library organization

[Rename, archive, or create independent copies](docs/library-management.md).

## Tags and metadata search

[Tag and search accepted metadata](docs/search.md).

## Local developer commands

[Run package-local setup, checks, tests, and builds](docs/development.md).

## Default storage location

[Resolve store paths and retain existing stores](docs/getting-started.md#default-storage-location).
