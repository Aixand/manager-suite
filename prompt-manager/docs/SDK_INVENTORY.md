# Prompt Manager feature and API inventory

The standalone core already implements ownership, rendering, publisher updates,
reconciliation, history, and immutable releases. The version 1 SDK exposes these operations through explicit package handles. Collections/folders and permanent deletion
remain separate feature work; publishing an SDK must not imply those exist.

The [version 1 SDK guide](python-sdk.md) describes the implemented public contract.
The original design recommendations below explain its direction; the delivery
status here distinguishes completed work from remaining feature/API refinements.

## Version 1 delivery

Implemented: `SDK_CONTRACT_VERSION`, frozen store-bound `PackageReference`, typed
JSON result contracts, explicit `Manager.package()`/`open_package()` handles,
`application_descriptor()` lookup, managed candidate validation, historical
comparison, and snapshot export. Identity-bound managed CLI operations use the
same handles. Existing Manager methods and persisted schemas remain compatible;
the constructor-refresh application overload has its own implementation.

Package handles provide one-call registration (`.content` and `.status`),
strict refresh, status, resolution, rendering, guarded edits,
review/inspection, reconciliation, history, restore, tracking, publication,
validation, comparison, and export. Contract/workflow tests cover these operations.

Remaining refinements: uniform selectors across managed/draft/release sources
and optional CLI coverage for bounded inspection and catalog authoring. Collections/folders, permanent deletion, and garbage collection
remain independent feature designs.

Internal domain workflows now take explicit store, discovery-path, registry, and
managed-engine dependencies instead of the Manager facade. Entry-level authoring
uses library discovery without resolving unrelated applications or managed content.
Architecture checks protect these boundaries; public SDK and persisted schemas
remain unchanged by that refactor.

Source and logical-selection APIs now expose typed descriptors, target unions,
resolution/render results, and list issue contracts. Normalized source lookup is
explicit through `source_descriptor()`. Public filesystem failures share the
package SDK's `PromptError` boundary; list error strings are preserved with
additive structured diagnostics. Historical source upsert shapes remain supported.

`check_store()` now provides a typed read-only integrity/availability report, with
matching `store check` CLI output. Storage tests cover actual process interruption
and contention; platform limits are documented in [storage reliability](storage-reliability.md).

Validated `backup_store()` / `restore_store()` and matching CLI commands preserve
supported owned records, history, releases, and mappings. External files are
explicitly excluded. See [backup and restore](backup-restore.md) for the implemented
archive contract and new-store-only restoration.

Managed library organization now supports independent personal duplicates, guarded
display names, and archive/unarchive. Archived entries are hidden from default
managed browsing but retained for resolution, history, integrity, and backup.
See [library management](library-management.md) for the implemented contract.

Guarded tags and `search_managed()` now support accepted metadata queries and
publisher/archived/customized/review filters. Missing descriptions are
reported for explicit guarded metadata refresh; queries never load bodies or
write indexes. See [search and tags](search.md) for the implemented contract.

## Feature coverage

“Available” means an explicit public operation exists. “Partial” means the
underlying representation or workflow exists but lacks a complete feature API.

| Feature | Current public API | Coverage and limits |
| --- | --- | --- |
| Personal prompt creation | `create_managed(name, content=..., role=...)` | Available for one prompt with a fixed body asset; create-only. Typed inputs and more complex recipes require manifest editing. |
| Owned pack import | `import_local(path, package_id=...)` | Available for native pack directories and complete snapshot JSON. Default identity follows the canonical path; explicit package IDs survive path changes. |
| Publisher registration and updates | `Package.register()`, `Manager.register_prompt()`, `ensure_registered(publisher, path, package_id=..., publisher_revision=..., version=...)` | Available. Registration returns effective rendered content and captured status, with verified-existing-content fallback after ingestion failure. Strict refresh/ensure also remain available. No remote polling. |
| Library listing and inspection | `list()`, `show()`, `catalog()`, `catalog_source()`, `managed_catalog()`, `managed_summaries()`, `managed_status()`, `managed_view()`, `resolve_managed()` | Available, with overlapping result shapes. `list()` covers drafts/releases; owned packages need the managed/catalog operations. Summaries do not verify content blobs; they must not imply a health check. |
| Accepted content editing | `save_managed(..., files, expected_revision=...)` | Available. Atomic multi-file changes validate the complete snapshot and rendering examples. Not an incomplete editor-buffer store. |
| Templates and input contracts | Pack definitions; `save_managed()`, `validate()`, rendering | Available through pack manifests: basic JSON types, defaults, enums, and template variables. No high-level input-definition authoring API or general JSON Schema engine. |
| Reusable composition | Pack sections, documents, dependencies, conditions | Available through manifests. Dependencies are captured in snapshots; changing their references requires available local releases. |
| Rendering | `render_managed()`, `render()`, `render_application()` | Available for text and role/content messages, with identity and section provenance. Distinct result schemas reflect the selection route. |
| Validation and rendering cases | `validate()`, accepted saves, registration, release | Available. Public `validate(source)` accepts draft/release sources; `Package.validate(files)` validates an unsaved managed candidate without saving. Cases check rendered output, not model behavior. |
| Pending update inspection | `managed_status()`, `managed_view(review=True)`, `preview_reconciliation()` | Available. Shows baseline, accepted content, incoming content, conflicts, and candidate validation. |
| Reconciliation | `reconcile(..., expected_revision=..., resolutions=..., take=...)` | Available. Explicit apply, guarded against stale state. Retaining user content acknowledges the incoming baseline. |
| History and restoration | `managed_history()`, `rollback_managed()` | Available. Restoration creates a new state while retaining the publisher baseline and pending update. |
| Historical pins | `revision=` on managed resolution, rendering, publication; managed targets | Available. Pins select managed state revisions, not publisher revision integers or release labels. |
| Publisher tracking controls | `set_managed_tracking(..., enabled, expected_revision=...)` | Available. Disabling tracking retains owned content; enabling it does not itself fetch or refresh. |
| Immutable publication | `release_managed()`, `release()`, `release_catalog()` | Available. Captures complete packs and dependencies. Publication does not advance logical mappings. |
| Snapshot and render comparison | `diff(before, after, ...)`, reconciliation preview | Available within a package through `Package.compare()`; partial across selection types. `diff()` supports draft/release sources, not direct managed-state selectors. Reconciliation compares the current baseline/user/upstream trio. |
| Content export and transfer | `Package.export()`, `resolve_managed()["snapshot"]`, `import_local()`, `backup_store()`, `restore_store()` | Available. Complete content can be serialized and imported; explicit SDK/CLI snapshot export and validated full-store backup/restore are available. External draft files remain excluded from owned-store archives. |
| Organization and search | `Package.set_tags()`, `Manager.search_managed()`, `Package.refresh_metadata()` | Guarded tags and body-free managed metadata search are available. Folders, collections, and body-content search remain separate features. |
| Duplication and rename | `Package.duplicate()`, `Package.rename()` | Available for managed packs. Copies have a new pack name and independent history; rename changes only display labels. |
| Archive and deletion | `Package.archive()`, `Package.unarchive()`; source/application unregister | Reversible archival is available. Permanent owned-content deletion and garbage collection remain unimplemented. |
| External draft authoring | `register_source()`, `sources()`, `show_catalog()`, `save_catalog_file()`, `release_catalog()`, `unregister_source()` | Available. Intentionally file-backed; catalog save edits one existing file at a time. Separate from owned multi-file edits. |
| Logical selections | `register_application()`, `application()`, `applications()`, `set_application_prompt()`, `resolve_application()`, `render_application()`, `unregister_application()` | Available as generic local mappings. Managed pins, releases, and explicit registered drafts are supported. Packaged fallback is explicit. |
| Content adoption | `adopt_legacy()` | Available. Preserves custom content and uses conservative reconciliation when its publisher ancestor is unknown. |
| Sync diagnostics | `report_managed_sync_error()` and status/render results | Available. Explicit refresh can raise while accepted content remains usable. Diagnostic reporting does not perform a refresh. |

The ownership, update, and reconciliation unit remains the whole pack. An edit
to one recipe can hold an incoming update containing several recipes. Keep that
unit in the first SDK to preserve shared-section consistency.

## SDK design concerns

1. **Inconsistent identities.** Registration calls use `package_id`; other managed
   calls use `identifier`. `publisher`, `publisher_id`, prompt names, pack names,
   catalog IDs, and logical names are different concepts. Use `publisher_id`,
   `package_id`, and `prompt_id` consistently in the new SDK, while preserving
   persisted schemas and documenting migration from existing parameter names.
2. **Overloaded application access.** `application(id)` returns a descriptor, while
   `application(id, bundled_pack=...)` constructs a handle that refreshes publisher
   content. Separate descriptor lookup, handle construction, and registration.
3. **Hidden mutation and failure handling.** The existing application handle
   registers during construction and catches refresh failures when an accepted
   copy remains usable. The new default should be explicit refresh with a visible
   diagnostic; constructing a handle or rendering must not refresh implicitly.
4. **Explicit result contracts.** Methods return dictionaries with differing
   fields, including raw state/history records. Version 1 now declares documented
   `TypedDict` results and `Literal` choices for managed and generic selection APIs
   using the standard library. Do not require callers
   to understand storage-only fields such as history links to perform ordinary work.
5. **Failure conventions.** Package, source, and logical-selection SDK boundaries
   normalize filesystem failures to `PromptError` while preserving domain codes.
   Lists retain per-entry issues, validation can raise, and review previews can
   return validation diagnostics. Other filesystem workflows retain their
   existing exception behavior; keep these distinctions explicit in their contracts.
6. **Incomplete source coverage.** Managed state cannot be passed uniformly to
   `show()`, `validate()`, or `diff()`. Add explicit managed selectors or dedicated
   managed operations before promising comparison and validation across sources.
7. **Uneven CLI coverage.** The CLI covers the primary managed lifecycle, but lacks
   bounded inspection and catalog-save/publication commands matching all Python
   methods. Define user-facing parity deliberately; diagnostic and migration helpers
   do not each need their own command.

## SDK contract direction

Keep one in-process Python SDK over the existing core. `Manager` remains the
entry point; do not introduce another policy engine or a server requirement.
An optional package handle can bind an identity and delegate to the same public
operations, with no registration side effect in its constructor.

| Contract family | Required operations |
| --- | --- |
| Owned packages | Create, import, list, inspect, resolve, validate a candidate, edit, render, compare, export a snapshot |
| Publisher lifecycle | Register/refresh, inspect status, preview reconciliation, apply reconciliation, change tracking |
| Revisions | List history, resolve/render a pin, restore content, publish a selected snapshot |
| External drafts | Register locations, inspect, edit, validate, compare, publish, unregister |
| Logical selections | Create/read/list descriptors, guarded mapping changes, resolve/render, guarded removal |

Use explicit selectors rather than guessing whether an arbitrary string is an
owned package, external path, or release. Keep convenient release strings where
their grammar is established. Constructors and inspection must not create a store
or perform publisher registration; local `Path.resolve()` is not a content import.

Distinguish the three version concepts:

| Value | Meaning |
| --- | --- |
| Content digest | Equality and integrity of a complete captured snapshot |
| Managed state revision | Opaque token for history pins and optimistic write guards |
| Publisher revision | Ordered nonnegative integer within one publisher/package stream |

Display `version` labels are descriptive. Release labels identify immutable
published content; neither is a substitute for publisher ordering. Keep the
existing stored `revision` field and use documentation/types to disambiguate it.

Managed edits, reconciliation, content restoration, tracking changes, and descriptor changes require
the observed revision. Creation is create-only. Registration is a distinct
idempotent publisher-ingestion operation governed by update policy. Publication
should identify the captured digest/state so callers can tell exactly what was
released. A successful preview or publication never changes accepted selections.

Do not add required runtime dependencies for typing, validation, transport, or
models. Preserve existing render schemas and stored objects while introducing
the SDK; replacing method names should follow an explicit compatibility policy.
The `unregister("sources", id)` alias and unguarded `UNSET` source upserts
should stay outside the preferred guarded SDK surface.

## Implementation sequence and acceptance

1. Define public identity, selector, result, and error types, plus compatibility
   rules for existing `Manager` methods. Separate application lookup from handles.
2. Expose explicit lifecycle operations with consistent arguments and no hidden
   registration. Reuse current domain policy and persistence.
3. Fill the first SDK gaps: managed candidate validation/comparison and explicit
   snapshot export. These are bounded content operations, not backup/restore.
4. Route supported CLI workflows through the same contract and add concise
   examples for personal prompts and publisher updates with user customization.
5. Test public imports and signatures, result shapes, read-only construction,
   explicit refresh failures, stale guards, pins, and CLI behavior. Retain stored
   fixtures and isolated installed-package verification.

Collections/folders, permanent deletion, and garbage collection need separate designs. They do not block an SDK covering the current lifecycle. In
particular, deletion must account for references from history, releases, and
logical selections before removing shared content objects.

Integrations begin after that contract is implemented and verified. Remote
discovery, background jobs, agent launch, and application startup policy remain
outside the standalone SDK.

## Implementation references

- [Public operations](../src/prompt_manager/manager.py) and
  [current convenience handle](../src/prompt_manager/interfaces/sdk.py).
- [Local authoring](../src/prompt_manager/managed/authoring.py),
  [accepted edits](../src/prompt_manager/managed/editing.py),
  [update policy](../src/prompt_manager/managed/updates.py), and
  [reconciliation](../src/prompt_manager/managed/reconciliation.py).
- [Draft inspection and editing](../src/prompt_manager/library/authoring.py),
  [comparison](../src/prompt_manager/library/comparison.py), and
  [logical resolution](../src/prompt_manager/applications/resolution.py).
- [Lifecycle tests](../tests/support/managed_lifecycle.py),
  [stored compatibility tests](../tests/integration/test_stored_compatibility.py),
  and [isolated execution tests](../tests/integration/test_standalone.py).
