# Package lifecycle SDK, version 1

Prompt packs and native directory packages can share accepted-content policy
without sharing a file format. `PackageLifecycle` is the public synchronous SDK
for that policy. `PACKAGE_LIFECYCLE_VERSION` is `1`. The existing prompt-specific
`Package` handle and `SDK_CONTRACT_VERSION` remain unchanged.

```python
from prompt_manager import Manager

prompts = Manager(home="/absolute/prompt-store", packs=[])
lifecycle = prompts.lifecycle()
status = lifecycle.ensure_registered(
    "publisher", "review", "/absolute/native-prompt-pack",
    publisher_revision=1,
)
owned = lifecycle.resolve("publisher", "review")
```

This uses the existing prompt format, validation, storage, digests, and releases.
Rendering continues through `Manager` or its prompt `Package` handle. Existing
store inspection, backup, and restore retain their prompt-format contracts.

## Other package formats

A dependent package supplies a trusted Python object implementing the exported
`PackageBackend` protocol:

```python
from prompt_manager import PackageLifecycle

# NativeBackend is supplied by the dependent package, not imported by Prompt Manager.
lifecycle = PackageLifecycle(NativeBackend(...))
status = lifecycle.ensure_registered("publisher", "review", source, publisher_revision=1)
candidate = lifecycle.validate("publisher", "review", file_changes)
saved = lifecycle.save(
    "publisher", "review", file_changes, expected_revision=status["revision"],
)
```

The backend owns its native package format, parser, file/path checks, limits,
content digests, and persistence. It can keep manifest/blob storage and byte
snapshots; no conversion to a prompt pack or rendered text is required. The
SDK owns accepted-state ordering, customization policy, reconciliation, history,
rollback holds, tracking, display labels, tags, archival, and metadata search.
There is one lifecycle engine, also used by existing prompt APIs.

The dependency is one-way. Prompt Manager imports only its own modules and the
Python standard library. It never discovers or imports external backend classes,
including when opening records. Constructing the SDK performs no IO.

## Backend contract

| Members | Responsibility |
| --- | --- |
| `home`, `schema`, `errors` | Absolute `Path`, managed-state schema, and tuple of backend exception classes |
| `identifier`, `check_digest` | Validate identities before deriving paths or reading content |
| `lock` | Context manager serializing all writers to this backend home |
| `read`, `write`, `registrations` | Managed JSON records; missing-aware reads; atomic writes; immutable history publication; enumerate `(publisher, identifier)` pairs |
| `capture`, `from_tree`, `edit` | Return a fully validated native snapshot from a source, proposed tree, or file changes |
| `snapshot_digest`, `store`, `snapshot` | Stable native identity; complete immutable content publication; verified loading |
| `tree` | Detached JSON-compatible comparison tree, preserving all meaningful native state |
| `describe` | Return exactly `digest`, `name`, `description` for the selected snapshot; name is nonempty text of at most 128 characters |
| `atomic_paths` | JSON pointers whose differing values require explicit choices, never text merges or recursive merging |

The backend may additionally implement `validate_description(value, identity)` to
enforce stricter native metadata rules. Backend methods can raise `PromptError`,
`OSError`, or a declared backend exception. SDK policy errors use `PromptError`;
filesystem errors crossing public SDK methods become `PromptError(code="io")`.

An adapter preserving a closed state schema can set
`store_content_metadata = False`. Content digests are still checked, but commits
do not add descriptive summary fields. Metadata search then reports summaries as
missing; bounded views can still describe the loaded native snapshot. Prompt
stores keep descriptive summaries enabled. An optional `before_registration(row)`
hook runs under the lock after the tracking check, allowing an acquisition adapter
to prevent local registration from replacing a remotely tracked resource.

`validate_identity` and `validate_publisher_metadata` perform read-only argument
validation. `record` returns validated raw managed state without derived status
fields. Trusted acquisition integrations can use `commit_record(record,
expected_revision=...)` **while holding the backend lock**. It verifies active
native content, checks the observed revision (`None` is create-only), and validates
the complete state before publishing history or the pointer. It does not fetch
content or choose update policy. Read-only observations never rewrite records.

Backends are trusted code, not a sandbox. The SDK does not add containment,
filesystem synchronization, or integrity guarantees missing from a backend.
Capture and edits must preserve original bytes and reject invalid packages before
activation. Snapshot loading must verify identity and content. Immutable content
must be written before current state can point to it. The backend's lock must also
be used by all writers sharing the store. Backend methods called under that lock must not
reacquire a non-reentrant lock.

The managed state schema uses the existing baseline/upstream/active digest model,
sequence, previous revision, and immutable history. Identifiers must be safe single
path components. State revisions use Prompt Manager's canonical JSON SHA-256
contract; native content digests remain backend-defined. The backend home must
contain only compatible managed records. Different package formats need separate
homes unless the backend explicitly dispatches and validates each format.

## Review trees and binary files

A recommended file tree uses `name`, a `directories` map of relative paths to
`true`, and a `files` map. File entries contain either `text` or `content_b64`, plus
an `executable` boolean. This preserves text bytes, binary data, execute flags,
and empty directories without introducing template semantics.

Return each binary payload's JSON pointer from `atomic_paths`, for example
`/files/assets~1image.png/content_b64`. The SDK unions these paths across the
baseline, accepted, and incoming trees. Binary differences require choices while
independent executable changes can merge. Deleting an edited file conflicts.
Disjoint text edits can merge; the backend validates the entire proposed package
before it can be accepted. Directory and permission changes participate in
comparison and content identity as defined by the backend.

`preview_reconciliation` returns complete trees and conflicts without activating
them. `view` offers bounded text and conflict previews using the file-tree shape
above. Binary previews expose size/hash rather than encoded bodies. Revision
guards reject stale edits, reviews, and organization changes. `validate` raises
on an invalid candidate and never saves it. `compare` compares historical accepted
content without rendering.

## Operations

`create` is create-only and disables publisher tracking. `ensure_registered`
strictly ingests publisher content; `register` also returns the effective snapshot
and can retain a verified accepted copy after ingestion fails. Neither executes
scripts or initiates model calls. Source acquisition belongs to the backend/caller.

`status`, `resolve`, `catalog`, `save`, `validate`, `compare`, `view`,
`preview_reconciliation`, `reconcile`, `history`, `rollback`, `set_tracking`,
`report_sync_error`, and `adopt_legacy` provide the shared lifecycle.
`rename` changes a display label only. `set_tags`, `set_archived`, `search`, and
`refresh_metadata` reuse existing organization policy. Search retains the existing
`pack_name` response field for compatibility, containing the native package name.

`release` and `export` require the optional exported `PackageDelivery` protocol.
Its `publish(snapshot, version, status)` and `export(snapshot, destination, status)`
callbacks run under the backend lock. They receive the exact accepted snapshot,
including when pinned by historical state revision. The backend owns native release
references, version validation, immutable labels, and safe native export. Missing
delivery support raises `unsupported`; there is no fallback conversion.

## Scope and migration

This SDK enables a Skill Manager adapter; it does not migrate Skill Manager or
declare its existing backend compatible without adaptation and qualification.
Native skill metadata/YAML validation, filesystem capture, manifests, blobs,
export guarantees, and release references remain that adapter's responsibility.
Prompt Manager does not depend on Skill Manager, PyYAML, resource-sources, or
aixand-platform. No runtime dependencies were added.

External backend stores are not automatically included in `Manager.check_store`,
`backup_store`, or `restore_store`: those APIs validate prompt stores. An external
backend must provide maintenance for its own native content and state records.
There is no automatic store relocation, digest conversion, release rebinding,
cross-format duplication, GitHub synchronization, worker, or CLI format switch.
Existing prompt schemas and APIs remain unchanged. Moving native storage or
adding broader maintenance support is a separate explicit compatibility change.
