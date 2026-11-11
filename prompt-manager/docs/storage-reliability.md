# Storage reliability and inspection

Prompt Manager retains its filesystem store and existing record schemas. Managed
mutations publish immutable content and history before replacing the current
state record. Inspection reports problems without choosing replacement content,
repairing records, or deleting files.

## Read only store checks

```python
from prompt_manager import Manager

manager = Manager(home="/absolute/path/to/store", packs=[])
report = manager.check_store()
print(report["ok"], report["counts"])
for finding in report["findings"]:
    print(finding["category"], finding["code"], finding["path"])
```

```sh
prompt-manager --home /absolute/path/to/store store check
prompt-manager --home /absolute/path/to/store store check --no-external
```

The SDK returns `StoreCheckResult` with schema `prompt-manager/store-check-1`.
Findings use the public `StoreFinding` type. The CLI emits the same JSON report on
stdout and exits 0 when `ok` is true, otherwise 1. An operational failure before a
report can be produced uses the existing JSON error envelope on stderr. Warnings
alone do not change the exit status.

| Category | Meaning |
| --- | --- |
| `corruption` | Invalid snapshot/record, missing referenced object, missing or mismatched current history, broken history link, or invalid sequence |
| `access` | An owned file/tree could not be inspected, including unsupported links or special files |
| `availability` | A registered external source is unavailable or a logical mapping cannot currently resolve |
| `unreferenced` | Valid objects/history outside the inspected reference roots, or temporary files from active/interrupted writes |
| `concurrent_change` | Local store fingerprints changed during the check; findings need a fresh observation |

Every finding has severity, category, code, path, and message. Findings originating
from domain or IO exceptions also contain a structured diagnostic. Counts cover
inspected record paths in `objects`, `releases`, `managed`, `managed-history`,
`sources`, and `applications`, plus error and warning totals. Unknown top-level
areas, lock files, and non-record auxiliary files are outside this check.

`ok` means no detected corruption/access errors and a completed, unchanged local
observation. `complete` is false when access or unsupported topology prevents the
inspection. `stable` compares local file/directory fingerprints before and after;
it does not certify an atomic snapshot. `references_complete` is false when valid
reference roots cannot be established. A missing store is an empty healthy result
with `exists=False`; inspection does not create it.

The check creates no locks, records, content, or directories. It uses existing
validators and bounded JSON reads. Initial enumeration refuses symlinks and
special files and stops deeper inspection if enumeration is incomplete, avoiding
blocking reads from unexpected file types. Readable malformed records are reported
individually. Large stores require a full scan; object validation does not retain
all snapshot content in memory.

## Reference and availability rules

Content references include accepted, baseline, upstream, and content adoption
snapshots; histories reachable from current state; immutable releases; and explicit
managed-state or digest-release mappings. Captured dependencies are validated
inside snapshots and do not require their original standalone release objects.

All stored history records are validated, including disconnected records. Valid
unreferenced artifacts are only classified when enumeration and reference checks
are complete and the local observation is stable. These findings describe known
store roots: references serialized outside the store are not discoverable. A
reference warning never authorizes deletion.

`include_external=False` / `--no-external` skips registered external-directory and
draft-target availability checks. Owned records and managed/release logical
mappings are still checked. Availability warnings do not imply owned-store
corruption and do not make `ok` false on their own. A missing referenced owned
content object is corruption; the checker never substitutes another prompt.

Checks are observations without writer locks. Run with writers idle when assessing
unreferenced artifacts. Fingerprints can reveal changes, but are not a transactional
or power-loss proof; external drafts can change independently of the local store.

## Write ordering and failure outcomes

Immutable publication writes a temporary file, flushes and syncs its data, links
it into place without overwriting an existing identity, and syncs its parent on
POSIX. Mutable records flush/sync a temporary file, atomically replace the target,
and sync its parent on POSIX. New directory entries are synchronized in parent
order. Draft-file replacement and source/application descriptor deletion also sync
the affected directory. Identical mutable retries re-sync the directory.

An exception does not always mean nothing changed. Failure before replacement
leaves the old record; failure after replacement but during directory sync may
leave the new complete record visible. Re-read state before retrying a guarded
mutation. Likewise, deletion can have taken effect before a directory-sync error
is reported. Accepted content/history may have unreferenced artifacts after an
interrupted operation, but no partial JSON record is deliberately activated.

The Windows byte-range lock protocol initializes a nonempty lock byte only when
the file is empty; acquiring an existing lock reuses that byte. Both platform branches release locks with their file handles/processes.
Immutable publication still requires filesystem hard-link support.

## Verification and limits

Automated tests terminate actual subprocesses after content publication, after
history publication, before current-record replacement, and after replacement.
They reopen the store, check integrity, and exercise guarded retries. Separate
processes compete to register, edit, reconcile, and publish. A writer blocked on
another process's lock proceeds after that process is terminated.

Primitive tests inject failures during data sync, link, replacement, and directory
sync. They verify complete readable records, immutable identities, retry behavior,
and synchronization ordering. Checker tests cover corrupt/missing records, pins,
digest-only mappings, unreferenced artifacts, external availability, changing
stores, inaccessible files, and read-only operation.

These checks have been run on Linux. The Windows lock protocol has isolated mocked
tests; native Windows filesystem/locking behavior has not been verified in this
environment. Python's standard library has no equivalent Windows directory-sync
operation here. No universal durability claim is made for power loss, network
filesystems, or filesystems with different hard-link/rename/fsync semantics.

Automatic repair and garbage collection remain outside this pass. Inspection
preserves evidence for those separately designed operations. [Validated store
backup and restore](backup-restore.md) now reuse this checker before publishing
an archive or activating a new store.
