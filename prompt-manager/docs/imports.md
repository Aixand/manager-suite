# Import content or register external drafts

Choose whether the manager owns captured content or an external directory remains
the authoring authority. Registration of a draft location does not copy its files.

## Choose an ownership model

| Task | API | Files needed later? |
| --- | --- | --- |
| Own a personal prompt | `create_managed()` | No external source |
| Capture a native pack or snapshot JSON | `import_local()` | No, after capture |
| Track publisher deliveries | `package.register()` / `refresh()` | Only on explicit refresh |
| Edit a directory as a draft | `register_source()` | Yes |
| Preserve the full store | `backup_store()` | Archive includes history and mappings |

## Import into your library

```python
status = manager.import_local("/absolute/path/to/pack", package_id="my-prompts")
package = manager.package("local", status["id"])
snapshot = package.resolve()["snapshot"]
```

This excerpt assumes an existing Manager. A native directory contains `pack.json`
and referenced UTF-8 assets. A complete snapshot JSON file is also accepted.
Capture embeds transitive dependencies. Referenced dependency releases must exist
locally while capturing a native draft; they are unnecessary after capture.

Supply `package_id` when moving sources: the default ID derives from the canonical
source path. Changed repeated imports are unordered publisher updates requiring
review; user edits remain active. Identical imports are idempotent.

```sh
prompt-manager managed import-local /path/to/pack --package-id personal
```

`package.export(destination)` writes portable snapshot JSON without overwriting
different content. This transfers accepted content, not history or mappings.
The [owned example](examples/owned-workflow.md) verifies export/import and backup
as separate operations.

## External drafts and logical selections

```python
from pathlib import Path

source = manager.register_source("authoring", [Path("/absolute/path/to/pack")],
                                 expected_revision=None)
```

Explicit `None` is create-only. For updates, use the observed descriptor revision.
`UNSET` retains legacy unguarded source upserts; prefer guarded writes.
`source_descriptor(id)` returns a normalized read-only lookup; `sources()` isolates
broken entries with string errors and structured diagnostics.

`catalog()`, `show_catalog()`, `save_catalog_file()` and `release_catalog()` provide
inspection and guarded draft authoring. Read-only source policy restricts catalog
edits, not direct filesystem access. Saving draft files, publishing a release, and
changing a logical selection are distinct operations. Missing/retargeted paths can
fail; registration does not make a draft immutable.
See [logical selections](integrations.md) and [SDK source contracts](python-sdk.md).

## Capture boundaries

Native capture validates the manifest, resolves assets within the pack directory,
normalizes line endings, and validates complete snapshots. Publisher directory
capture reads twice and rejects observed changes. This detects changes rather than
providing a filesystem-wide transaction. Keep source writers idle during import.
Remote metadata is inert provenance; the core performs no remote acquisition.

Read [snapshot capture](../src/prompt_manager/library/snapshots.py),
[filesystem capture](../src/prompt_manager/sources/filesystem.py), and
[import tests](../tests/integration/test_sources.py) before changing these rules.
