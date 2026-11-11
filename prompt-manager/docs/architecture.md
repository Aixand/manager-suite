# Prompt Manager architecture

Prompt Manager is a standalone Python library and CLI with no third-party runtime
dependencies. The [feature and API inventory](SDK_INVENTORY.md) describes its
scope; the [managed lifecycle](MANAGED.md) defines customization and update behavior.

## Ownership

| Module | Responsibility |
| --- | --- |
| `prompts/` | Definitions, inputs, templates, composition, validation, rendering, examples |
| `library/` | Draft capture/authoring, catalog, comparison, immutable releases |
| `library/discovery.py` | Draft/release discovery and entry lookup without application or managed inspection |
| `library/catalog.py` | Aggregate library entries, logical application resolutions, and managed summaries |
| `library/integrity.py` | Read-only validation, reference tracing, and availability inspection |
| `library/backup.py` | Bounded store archive capture, checksum validation, and staged restoration |
| `managed/` | Owned creation/import, accepted state, publisher updates, reconciliation, history, inspection |
| `managed/organization.py` | Guarded labels/archive/tags, explicit metadata refresh, independent copies |
| `managed/metadata.py` | Tag normalization and accepted content-description validation |
| `managed/search.py` | Read-only metadata queries and status filtering without body loading |
| `applications/` | Generic logical prompt descriptors, targets, resolution |
| `sources/` | Local authoring-location registry, filesystem capture, draft materialization |
| `storage/` | Paths, bounded reads, integrity, locks, atomic records and immutable objects |
| `manager.py` | Public operations and coordination |
| `interfaces/contracts.py` | Versioned public identities and typed JSON results |
| `interfaces/sdk.py` | Explicit synchronous package handle; delegates to public Manager operations |
| `interfaces/legacy_application.py` | Compatibility for the constructor-refresh application handle |
| `interfaces/cli/` | Command parsing and presentation |

The engine consumes captured snapshots and inputs. It does not fetch, persist,
read source files, or call a model. Storage has no update policy. Managed edits
and filesystem draft edits share validation and snapshot contracts but preserve
their different ownership semantics.

Public `Manager` methods coordinate these modules. Domain workflows receive their
actual dependencies: the release store, configured draft paths, registry, and
managed state engine where required. They do not receive or call back into the
Manager facade. Snapshot capture, discovery, comparison, authoring, publication,
and logical resolution call their owning functions directly.

Single-entry catalog lookup, inspection, edits, and publication use library
discovery directly. They do not resolve unrelated logical application mappings or
inspect managed packages. The full `catalog()` operation explicitly composes those
observations. Content operations therefore do not import the catalog aggregator,
application resolution, or managed policy. No generic service container or second
configuration object is required.

## Owned lifecycle

`managed/authoring.py` implements local creation and imports directly. Creation is
create-only. Imports capture a native pack directory or a complete snapshot JSON
file; repeat imports use stable identity and preserve customization. Publisher
registration captures and validates content before changing accepted state.

`managed/updates.py` owns ordering and activation. `managed/reconciliation.py`
owns comparison, merge proposals, conflict resolution, and explicit adoption.
`managed/history.py` owns historical selection and rollback. A restored version
is held against automatic publisher advancement until explicitly reconciled.

Managed mutation holds the managed-store lock, publishes content/history first,
and atomically replaces the current record last. Failed writes can leave
unreferenced objects/history but do not activate incomplete content. Guarded
writes reject stale observed revisions. A single manager-wide lock keeps the
initial concurrency design simple.

## Interfaces and boundaries

CLI handlers call public methods without implementing update policy. Local
operations are synchronous; parser construction imports no external package.
`interfaces/sdk.py` implements the version 1 explicit package handle, documented
in [the Python SDK guide](python-sdk.md). Construction binds a validated reference
without reading package content or refreshing a publisher. Its methods delegate to
public Manager operations and normalize filesystem failures at the SDK boundary.
The constructor-refresh handle is isolated in `legacy_application.py`.
Source and logical-selection Manager methods share the same filesystem-error
boundary from `errors.py`; existing domain codes pass through. `errors.py` also
owns diagnostic conversion used by registration/listing/catalog workflows, without
importing interface types. Domain code still raises ordinary domain/IO failures;
the public boundary supplies the SDK exception contract.

`interfaces/contracts.py` describes target unions, source and application
descriptors, resolution/render results, and per-entry registration issues. Existing
source return variants remain explicit. Descriptor-list issues preserve string
errors and add structured diagnostics; persisted schemas do not change.

The explicit `Package.register()` operation returns `RegistrationResult.content`
and `.status` through `Manager.register_prompt()`. `managed/registration.py`
shares ingestion policy with strict refresh, captures effective content and status
under one managed lock, and verifies a retained copy before returning it after
failed ingestion. Manager renders that captured snapshot after releasing the lock;
later edits cannot mix a different state into its result. Invalid inputs or missing
prompt selection still raise rather than returning unusable content.


`managed/workspace.py` implements candidate validation, historical comparison, and
snapshot export. Shared comparison logic remains in `library/comparison.py`.
These operations reuse captured snapshots and existing validation/publication
primitives; they do not implement another update policy. Stored schemas and
Manager methods remain compatible. The installed package includes type
information without a typing-library dependency.

Generic logical selections can point to managed revisions, releases, or explicit
external drafts. They require no application executable or provider package.
Call-scoped packaged fallback is explicit. No tool-specific bootstrap, consumer
adapter, remote discovery, source worker, or job scheduler belongs in the core.

## Storage compatibility

| Location | Content |
| --- | --- |
| `objects/` | Immutable captured snapshots |
| `releases/` | Immutable pack/version labels |
| `managed/` | Current accepted state and publisher baseline/upstream |
| `managed-history/` | Immutable state revisions |
| `sources/` | External local authoring locations |
| `applications/` | Generic logical prompt selections |
| `locks/` | Cross-process mutation locks |

Existing prompt/snapshot/state schemas remain readable. Stored `source` metadata
records provenance and is validated as an object. `source_review`/`hold` and
adoption state preserve pending reviews and retained content. Content operations
leave consumer, application-default, and import-job files untouched. Explicit
`adopt_legacy()` preserves content with an unknown ancestor for conservative
reconciliation.

Mutable records and immutable publication use fsynced temporary files and sync
parent directories on POSIX. Newly created directory entries, draft-file
replacement, and descriptor deletion use the same directory-sync primitive.
Immutable publication requires hard links. Failures after replacement can leave a
complete new state visible, so callers must re-read before guarded retries.

`check_store()` performs a read-only audit using existing validators and reference
roots, without creating locks or selecting repairs. Before/after fingerprints flag
observed changes; this is not an atomic snapshot. See [storage reliability](storage-reliability.md)
for result semantics, interruption tests, and explicit platform/durability limits.

## Managed organization

Organization policy belongs to `managed/organization.py`. Label and archive changes
use guarded state commits; duplication captures content under the managed lock and
creates a new local baseline/history. Interfaces delegate rather than implementing
filtering or copy policy. `managed/inspection.py` applies the archive browsing
filter, while direct resolution and integrity/backup scanning remain unfiltered.

The optional archived flag is validated by the state reader; absent flags preserve
public result shapes. Display metadata does not modify snapshot digests or IDs.
Copies change the root pack name for the new release namespace while retaining
recipe IDs and embedded dependencies. [Library management](library-management.md)
explains visibility, tracking, state revisions, and reader compatibility.

## Metadata search

Managed commits accept an optional already-captured snapshot to update a
content-linked name/description summary. Every content-changing owner supplies
its validated snapshot; metadata-only mutations reuse the existing summary.
This keeps description freshness centralized without introducing reads in search.
The integrity checker compares summaries to authoritative content objects.

`managed/search.py` scans validated state records only and reports missing
summaries and per-record issues explicitly. It never imports content loaders,
writes index files, or opens prompt bodies. Guarded explicit metadata refresh can
populate older states. [Search and tags](search.md) documents completeness and
matching rules. No database or background indexing service is required.

## Store archives

Backup captures supported record bytes using the shared read-only
`storage/inventory.py` scanner, verifies unchanged source fingerprints, and runs
the existing integrity checker against the captured store. Archive checksums cover
raw record bytes. Immutable publication prevents replacing an existing archive.
Restore manually validates/writes archive entries to a private sibling workspace,
checks the full staged store, and promotes it into an unused home path. It does
not import the Manager facade or rewrite persisted identities/revisions.

Supported owned records are included even when unreferenced. External source paths
remain metadata; external files, locks, temporary buffers, and unsupported
areas are excluded explicitly. [Backup and restore](backup-restore.md) documents
schemas, bounds, interruption semantics, and platform limits. The CLI store handlers
call the same public methods as SDK users.

## Development and verification

`dev.py`, `dev.sh`, and `dev.ps1` operate from this checkout. Python/setuptools
build tooling is distinct from the dependency-free installed runtime.

Architecture tests enforce standard-library-only runtime imports, pure engine and
storage boundaries, no domain imports or parameters for the facade, no facade
self-injection into domain operations, and no import cycles. Content operations
are also checked against dependencies on catalog aggregation/application/managed
resolution. Behavioral tests cover prompt composition and the complete owned lifecycle.
Stored fixtures verify unchanged historical render results. Isolated tests copy
only the package and execute with `-I -S`, outside the repository, with network
access denied. External integration verification belongs to the integration's
own package.
