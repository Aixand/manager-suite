# Managed library organization

Managed packages support independent duplication, display-name changes, and
reversible archival. These operations do not delete content or rewrite existing
logical mappings, release labels, or historical pins.

## Display names

```python
package = manager.package("publisher", "main")
observed = package.status()
renamed = package.rename("My review instructions", expected_revision=observed["revision"])
```

`rename()` changes only the optional `label` on the managed state. Publisher/package
IDs, the pack's stored name, recipe IDs, source files, content digest, and existing
references stay unchanged. Labels must be nonblank UTF-8 text of at most 128
characters. Pass `None` to clear the override. Bounded `inspect()` exposes the label
with the package ID as its fallback; `name` still identifies the captured pack.

A rename creates a guarded history revision. Repeating the same label with the
current guard is a no-op. Metadata edits invalidate older edit/reconciliation
revision tokens, even though the content digest stays unchanged. Existing history
pins still read their original state and content. Content restoration preserves
the current label and archival setting rather than restoring historical metadata.

## Archive and unarchive

```python
archived = package.archive(expected_revision=renamed["revision"])
all_packages = manager.managed_catalog(include_archived=True)
visible = package.unarchive(expected_revision=archived["revision"])
```

Archived packages are hidden from the managed lists in `managed_catalog()`,
`managed_summaries()`, and `catalog()` by default. Pass `include_archived=True` to
include them. This flag also applies to the CLI managed list and full catalog.
Other catalog sections, such as published releases and application mappings, retain
their own entries. Invalid state records are reported rather than hidden by a
malformed archival flag.

Archival is visibility only. It does not stop publisher tracking, freeze content,
disable rendering, remove mappings, or delete files. Direct handles and logical
selections continue working. Eligible publisher updates can activate unmodified
archived content; customized content still requires reconciliation. Registration
and publisher updates preserve the archival flag and display label and do not
unarchive automatically. Use the separate tracking control to stop ingestion.

Archive/unarchive require the current state revision. Repeating the current setting
is a no-op; stale guards still fail. Integrity checks and full-store backups always
inspect/include archived packages, regardless of browsing filters. Backup/restore
preserves labels and archival state exactly.

## Independent copies

```python
copied = package.duplicate("personal-review", label="Personal review")
personal = manager.package("local", copied["id"])
result = personal.render("review", inputs)
```

`duplicate(target_id, revision=None, label=None)` copies the whole accepted pack.
Omit `revision` for current accepted content or select a historical state token.
The destination is a new package under publisher `local`; an existing destination,
including an archived package, cannot be overwritten. Use a different target name
from the source pack name to give the copy a separate release namespace.

The new target ID becomes the copied pack's name. Recipe IDs, section/file names,
rendering cases, and captured dependencies remain intact, so callers still select
the original recipe ID when rendering. Display labels and ordinary instruction text
are not automatically rewritten. Publishing the copy uses its new pack name and
the existing immutable release rules; other packs can still conflict on an already
used release label/name.

The copy starts with one history revision and a baseline equal to its copied
content. It is unarchived, has no inherited publisher revision, pending update,
migration state, source diagnostics, or custom label unless supplied explicitly.
Tracking starts disabled so automatic registration cannot replace it. Enable
tracking explicitly before intentionally adopting a new publisher stream. Future
edits, reconciliation, archival, and publication of the source do not advance the
copy. The source's state/history and logical mappings are untouched.

Copies own complete validated snapshots. Original source files or standalone
objects for embedded dependencies are unnecessary. Object storage can share
immutable content internally; independent ownership does not require rewriting
captured dependencies or allowing mutable shared objects.

## Tags and search

[Metadata search and tags](search.md) provide guarded `set_tags()` updates and
`Manager.search_managed()` with publisher, tag, archival, customization, and pending
review filters. Copies retain the selected source state's tag values independently.
Content restoration preserves current tags. Search uses accepted pack descriptions
captured in state records; it does not open bodies or silently migrate old records.

## SDK and CLI

Package methods return the resulting `ManagedStatus`. The Manager equivalents are
`rename_managed(publisher_id, package_id, label, expected_revision=...)`,
`set_managed_archived(..., enabled, expected_revision=...)`, and
`duplicate_managed(publisher_id, package_id, target_id, revision=..., label=...)`.
Duplication is create-only and captures the source while holding the managed lock;
it does not require a destructive source-write guard.

```sh
prompt-manager managed rename publisher main --label 'My instructions' --expected-revision STATE
prompt-manager managed rename publisher main --clear-label --expected-revision STATE
prompt-manager managed archive publisher main --expected-revision STATE
prompt-manager managed unarchive publisher main --expected-revision STATE
prompt-manager managed list --include-archived
prompt-manager catalog --include-archived
prompt-manager managed duplicate publisher main personal-review --label 'Personal review'
```

Replace `STATE` with the most recently observed token after each mutation. All new
commands use the normal JSON result/error conventions. Permanent deletion and
object garbage collection are not included.

## Stored compatibility

Managed state/history retain the `prompt-manager/managed-1` schema. `label` is an
existing optional field; `archived`, `tags`, and `content_metadata` are optional
additions described in their feature guides. Untouched stored
records are not rewritten, and absent `archived` means false. New operations
validate their metadata and commit history before replacing current state using
the existing guarded write protocol. Older strict readers that do not recognize
the new fields must be upgraded before reading newly written records.

Full-store archives preserve these records byte for byte. Archive handling and
native-platform limits in [backup and restore](backup-restore.md) still apply.
