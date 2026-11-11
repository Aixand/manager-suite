# Owned prompt registrations

`Manager.ensure_registered()` imports a complete, validated prompt snapshot into
the selected Prompt Manager store. Prefer the [version 1 SDK](python-sdk.md)
for explicit package handles without constructor refresh. Rendering through a package handle or a
`managed` application target reads this owned snapshot, never the publisher files.
Dependencies remain embedded and do not need their original release objects later.

```python
from prompt_manager import Manager

manager = Manager(home="/absolute/prompt-store", packs=[])
prompts = manager.application(
    "publisher", bundled_pack="/installed/publisher/packs/publisher",
    publisher_revision=12, version="1.4.0",
)
result = prompts.render("review", {"request": "Explore the idea", "sources": []})
status = prompts.status()
```

The handle ensures registration once when constructed. `refresh()` checks the
publisher again; each `render()` captures the current accepted managed revision.
`package_id="default"` is independent of the installation path and display version.
Use another stable package ID for additional packs from the same publisher.
Explicit registration has the same contract:

```python
status = manager.ensure_registered(
    "publisher", "/installed/publisher/packs/publisher", package_id="default",
    publisher_revision=12, version="1.4.0",
)
```

## Update policy

The manager records a publisher baseline, the active content digest, and the newest
publisher snapshot. Content digests cover the pack definition, referenced text,
and captured dependencies. Version labels describe releases; they do not order them.
`publisher_revision` is a nonnegative integer ordered within a publisher/package.

* First registration imports and activates the package.
* Repeated registration of identical content is idempotent.
* A newer ordered update activates automatically if the active copy has no edits.
* A customized copy remains active while an update awaits reconciliation.
* Different unversioned content requires review, since hashes cannot establish age.
* Older ordered registrations are ignored. Reusing an ordered revision with other
  content is a conflict. An ordered stream cannot be replaced by an unversioned caller.

Explicit ensure/refresh calls raise on invalid publisher content and preserve the
previous active snapshot. The failure is retained as `sync_error`. Application
construction can continue using an existing valid copy after a refresh failure;
initial registration failures still raise. `render()` includes the captured managed
status as `resolution`, including any sync error and reconciliation requirement.

The status fields include `publisher_id`, `id`, `revision` (the guarded state token),
`active_digest`, `baseline`, `upstream`, `customized`, `tracking`,
`reconciliation_required`, `update_status`, and `sync_error`. Baseline/upstream each
contain `digest`, ordered publisher `revision`, and display `version`.

UIs should display "Publisher update needs review. Your customized version is still
active" whenever `reconciliation_required` is true. Notifications do not adopt updates.

## Editing and reconciliation

`save_managed(publisher, package_id, files, expected_revision=...)` accepts one atomic
mapping of relative paths to text, or null to delete a file. `pack.json` can be edited
together with its assets. The complete resulting snapshot and rendering examples
must validate. Saves create immutable content/history; publisher files are untouched.
Invalid authoring buffers should remain in the editor until valid; this API saves
accepted revisions rather than incomplete editor drafts.

```python
status = manager.managed_status("publisher", "default")
status = manager.save_managed(
    "publisher", "default", {"sections/review.md": "Custom instructions.\n"},
    expected_revision=status["revision"],
)
preview = manager.preview_reconciliation("publisher", "default")
```

Preview returns `managed_changes`, `publisher_changes`, a proposed `candidate`,
`conflicts`, `valid`, `validation_error`, and the guarded `revision`. JSON pointers
identify conflicts; every side distinguishes absent values from JSON null. Disjoint
line changes merge automatically in the proposal. Dependency snapshots are atomic
units so their captured digests cannot be separated from their content.

To apply a clean proposal, call `reconcile(..., expected_revision=preview['revision'])`.
For conflicts pass `resolutions={pointer: {"take": "managed"}}`, `{"take": "publisher"}`,
`{"take": "baseline"}`, `{"value": replacement}`, or `{"delete": True}`. Unknown choices,
unresolved conflicts, stale reviews, and invalid results fail before activation.
Alternatively `take="managed"` or `take="publisher"` accepts an entire side.

Applying advances the publisher baseline even when retaining the exact managed
content. The same publisher update therefore does not request reconciliation again.
Preview never changes active content. A new incoming update or editor save invalidates
an older apply token. A newer pending update retains earlier snapshots in history.

## History, pinning, and retained registrations

`managed_history()` returns newest-first immutable state records.
`resolve_managed()` returns the owned snapshot and its status. Both resolve and
render accept `revision=STATE_TOKEN` to pin a historical active snapshot. A rollback
via `rollback_managed(..., revision, expected_revision=...)` selects historical content
as a new active revision while preserving the current publisher baseline/pending update.

`set_managed_tracking(..., False, expected_revision=...)` stops publisher tracking
without deleting owned content. Automatic registration respects this retained stop;
explicitly enable tracking before refreshing again. Omitted packages are retained.
Automatic garbage collection and destructive library deletion are not included.

Managed state is stored in `managed/PUBLISHER/PACKAGE.json`, immutable state history in
`managed-history/PUBLISHER/PACKAGE/STATE_DIGEST.json`, and content in the existing
`objects/CONTENT_DIGEST.json`. Mutations use a manager-wide managed lock and publish
immutable objects/history before atomically replacing the active state record.
An interrupted write may leave an unreferenced object or history record; it does not
activate partial content. Read-only inspection of an absent store does not create it.

## Existing consumers and migration

The filesystem-authoring/source APIs and released references keep their
existing behavior. Existing source/application registrations are not silently
converted. Consumers migrate by supplying bundled packs through the convenience handle,
or ensuring an owned registration and selecting a managed application target:

```python
target = {"kind": "managed", "publisher_id": "publisher",
          "package_id": "default", "prompt": "review"}
# Optional target["revision"] pins a historical managed state.
# Use register_application() or guarded set_application_prompt() to select it.
```

`render_application()` and its CLI retain their schemas and add `resolution.managed`
for this target. `catalog()` includes managed content, drafts, releases, and generic application mappings;
the tool-specific `consumers` field has been removed.
`release_managed(publisher, package_id, version, revision=None)` publishes an accepted
owned snapshot through the existing immutable release contract.
Automatic application bootstrap/migration is outside the standalone core. Use
explicit registration and guarded mapping updates to choose content.

`adopt_legacy(publisher, package_id, legacy_path, publisher_path, ...)` preserves the
imported content as active. When it differs from the bundled publisher, status reports
`baseline_known=False`. Review then compares the two sides conservatively: different
values require a choice; no invented ancestor is used to merge them automatically.
Applying the decision establishes a known publisher baseline for later three-way reviews.

## CLI

All `managed` commands emit JSON, including status/provenance:

```sh
prompt-manager --home /tmp/prompts managed ensure publisher default /path/to/pack --publisher-revision 12
prompt-manager --home /tmp/prompts managed status publisher default
prompt-manager --home /tmp/prompts managed render publisher default review --inputs @inputs.json
prompt-manager --home /tmp/prompts managed save publisher default --files @changes.json --expected-revision STATE
prompt-manager --home /tmp/prompts managed preview publisher default
prompt-manager --home /tmp/prompts managed apply publisher default --resolutions @choices.json --expected-revision STATE
prompt-manager --home /tmp/prompts managed apply publisher default --take managed --expected-revision STATE
prompt-manager --home /tmp/prompts managed history publisher default
prompt-manager --home /tmp/prompts managed rollback publisher default OLD_STATE --expected-revision STATE
prompt-manager --home /tmp/prompts managed tracking publisher default off --expected-revision STATE
prompt-manager --home /tmp/prompts managed release publisher default --version v1
```

## Local authoring

`create_managed(name, content="", role="user")` creates a personal prompt under
publisher `local`, with the same prompt and package name. It is create-only and
validates content before activation. No worker or background job is involved.

`import_local(path, package_id=None)` captures a native pack directory or a complete
snapshot JSON file. Without an explicit package ID the canonical path determines
identity. Repeated content is idempotent; changed content is an unordered update
requiring review, with user edits kept active. Snapshots returned by
`resolve_managed()` include dependencies and can be saved as JSON for transfer.
This is content transfer, not a full history/store backup.

```sh
prompt-manager managed create review --content 'Review carefully.' --role system
prompt-manager managed import-local /path/to/pack --package-id personal
```

Remote source metadata is retained as inert provenance; rendering, history,
and reconciliation require no remote transport or external source package.


## Library organization

[Library management](library-management.md) documents guarded display names,
archive/unarchive, and independent personal copies. Archival affects browsing
visibility only; direct resolution, publisher tracking, and reconciliation keep
their existing behavior. Metadata writes create managed history revisions without
changing accepted content. Copies start separate local histories and do not inherit
pending publisher updates or tracking.
