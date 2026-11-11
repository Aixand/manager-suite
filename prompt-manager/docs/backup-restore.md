# Store backup and restore

Prompt Manager backs up supported store records to a validated ZIP archive and
restores them into a new store. Owned content, history, releases, and registration
metadata are preserved byte for byte. External draft files are not included.

## Back up a store

```python
from prompt_manager import Manager

source = Manager(home="/absolute/path/to/prompt-store", packs=[])
backup = source.backup_store("/backups/prompts.zip")
print(backup["digest"], backup["file_count"])
print(backup["external_sources"], backup["excluded"])
```

```sh
prompt-manager --home /absolute/path/to/prompt-store store backup /backups/prompts.zip
```

The archive must be outside the source store. Backup reads the source without
creating locks, changing records, or creating an absent source store. It captures
records in a private temporary directory, checks that source fingerprints did not
change during capture, and validates the captured store using the integrity checker.
Observed changes raise `conflict`; corrupt or incomplete captured stores are rejected.
Use writers-idle operation when a coordinated snapshot is required: fingerprints
and validation are not a global transaction across arbitrary external writers.

The written archive is read back to verify its manifest, entry sizes, and checksums.
It is published only after capture, store validation, and archive verification succeed. Existing
archives are never overwritten. An identical archive retry is idempotent; different
content at the same destination raises `conflict`. Use a new destination for another
backup revision. Output files are created privately, synced, and published through
the existing hard-link filesystem requirement.

## Restore into a new store

```python
restored = Manager(home="/absolute/path/to/restored-store", packs=[])
result = restored.restore_store("/backups/prompts.zip")
report = restored.check_store()
```

```sh
prompt-manager --home /absolute/path/to/restored-store store restore /backups/prompts.zip
prompt-manager --home /absolute/path/to/restored-store store check
```

The target home must not exist. An existing directory is rejected even if empty;
there is no in-place overwrite, merge, or replace option. Constructing a Manager
with that home does not create it. Missing parent directories may be created.

Restore validates the manifest and checksums, writes records into a private sibling
staging directory, then runs the integrity checker before promoting the complete
directory. Corrupt/incomplete archives never activate a target. A destination that
appears during validation causes a conflict. Restore is intended for an unused
location with no competing writers.

State revisions, publisher revisions, pending updates, customization, tracking,
sync diagnostics, logical mappings, and immutable release labels remain unchanged.
Managed-history pins continue to select the same content. Provenance for logical
resolution reflects the new store home; old serialized `PackageReference` values
remain bound to their original store. Create a new package reference through the
restored Manager instead of silently rebinding old references.

## Included and excluded data

| Included | Excluded |
| --- | --- |
| Content objects in `objects/` | Publisher source directories and external draft files |
| Current accepted state in `managed/` | Lock files and temporary write buffers |
| All valid immutable records in `managed-history/` | Removed tool-specific consumers, jobs, and defaults metadata |
| Immutable release labels in `releases/` | Unknown top-level areas and non-record auxiliary files |
| Source descriptors in `sources/` | Original filesystem ownership, timestamps, ACLs, and permissions |
| Logical mappings in `applications/` | Executables, services, and application/project data |

Valid unreferenced content and history are included, with warnings retained; backup
is not garbage collection. Malformed supported records are rejected rather than
silently omitted. The manifest and result list excluded paths relative to the
source home. External source IDs/paths are listed separately with
`external_drafts_included=False`.

External paths are preserved in registration metadata, without copying, relocating,
or rewriting them. They can be unavailable after restore, reported as availability
warnings by `check_store()`. Owned content and releases remain usable. Cross-platform
path conversion is not implemented; incompatible source-path metadata is rejected
rather than changing descriptor revisions. Stage validation skips external draft
availability but still checks managed/release mappings and owned reference integrity.
Pre-existing unresolved mappings can remain warnings.

## Archive format and result contracts

The archive contains `manifest.json` with schema `prompt-manager/store-backup-1`
and the exact supported relative record paths. Its `files` map records each file's
byte size and raw-byte SHA-256 digest. This is distinct from a canonical snapshot
or managed-state digest. The manifest also records the source store, excluded paths,
and external source metadata. Archive timestamps are fixed to avoid needless changes
when backing up identical source bytes in the same environment.

Restore rejects unsupported schemas, duplicate/unlisted entries, noncanonical paths,
path traversal, links, encrypted entries, checksum/size mismatches, and invalid store
reference graphs. It writes files explicitly rather than using ZIP extraction paths.
Stored/deflated ZIP entries are supported. Checksums detect corruption; archives are
not signed or encrypted.

Version 1 supports up to 100,000 records, 32 MiB per record/manifest, and 1 GiB of
uncompressed record data. Physical archive size and declared entry totals are also
bounded. Exceeding limits raises `size`; no records are silently truncated or skipped.
Archive creation needs temporary space for captured records plus the archive;
restoration needs space for a sibling staged store.

SDK results use `StoreBackupResult` / `StoreRestoreResult`, with schemas
`prompt-manager/store-backup-result-1` / `prompt-manager/store-restore-result-1`.
They contain archive path/digest, original store path, file count, uncompressed
content bytes, external-source/exclusion information, and validation warnings.
Restore additionally returns the target `store`. Warning paths are archive-relative.
Successful results have `validated=True`. CLI commands emit these results as JSON
and exit 0; failures use the existing stderr error envelope and exit 1.

## Interruption and platform behavior

Before publication, failure leaves no target archive/store visible. After archive
linking or restored-directory promotion, an error or process exit can leave a
complete result visible even if the caller received no success response. Re-open
and inspect it; do not assume an exception rolled back publication. Identical archive
publication can be retried, while restore will reject an already-published target.

Normal failures clean temporary workspaces. Abrupt process termination can leave
private backup/staging directories or temporary archives; they are not automatically
promoted or deleted. An interrupted restore does not change the source store or
archive. Directory-entry synchronization is implemented on POSIX; the native Windows
and power-loss limits in [storage reliability](storage-reliability.md) still apply.
Native Windows backup/restore has not been verified in this environment.
