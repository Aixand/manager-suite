# Owned skill registrations

`Manager.ensure_registered()` captures and validates the complete native skill
directory into Skill Manager's own object/blob store. Source files are never edited
by managed operations. Owned skills remain available if the publisher path disappears.

```python
from skill_manager import Manager

manager = Manager("/absolute/skill-store")
skills = manager.application(
    "pathspec", bundled_skills={"review": "/installed/skills/code-review"},
    publisher_revision=12, version="1.4.0",
)
resolved = skills.resolve("review")
receipt = skills.export("review", "/existing/consumer-parent/code-review")
statuses = skills.status()
```

Stable IDs (`review` above) belong to the publisher and survive installation moves.
They need not match native skill names. Exports still require the native name as
their destination basename. Resolution returns a verified manifest, digest, owned
blob locations, and managed `resolution` status. Export materializes original bytes,
executable flags, hidden files, and empty directories with the existing no-overwrite
and atomic publication guarantees. Native activation remains the consumer's responsibility.

The handle ensures each supplied skill on construction; `refresh()` checks again.
`resolve()`/`export()` use accepted owned snapshots and do not read publisher files.
Initial import failures raise. If a startup refresh fails but a valid copy already
exists, construction retains that copy and exposes `sync_error` in its status.
Explicit refresh failures raise. Rendering does not execute skill scripts.

## Update and reconciliation contract

The [Prompt Manager integration contract](prompt-manager-integration.md)
describes the shared lifecycle. Skill Manager delegates accepted-state policy to
Prompt Manager's public `PackageLifecycle` SDK through a native skill backend.
Prompt Manager never imports Skill Manager. Native storage, validation, immutable
release namespaces, and export guarantees remain unchanged. The skill lifecycle
tests qualify the integration, including old-format stores.

* `ensure_registered(publisher, path, skill_id=..., publisher_revision=..., version=...)`
  imports the first snapshot and idempotently observes repeated content.
* Ordered publisher revisions are nonnegative integers. Version text is descriptive.
  Older registrations cannot downgrade content; reusing a revision with changed
  content fails. Unordered changed content requires review.
* Uncustomized owned copies adopt newer ordered updates automatically. Customized
  copies remain active while `reconciliation_required` is true.
* `save_managed(publisher, skill_id, files, expected_revision=...)` atomically saves
  accepted content. File values are `{"content": bytes, "executable": bool}` or null
  for deletion. The resulting complete native package must validate.
* `preview_reconciliation()` returns both change sets, a proposed tree, conflicts,
  validation results, and a guarded state `revision`. It never activates content.
* `reconcile(..., expected_revision=..., resolutions=...)` applies a validated result.
  Choices use JSON pointers and `{"take":"managed"}`, `{"take":"publisher"}`,
  `{"take":"baseline"}`, `{"value":...}`, or `{"delete":true}`. An entire side can be
  selected with `take="managed"` or `take="publisher"`. Unresolved/stale reviews fail.
* Reconciliation advances the publisher baseline even when keeping the user's content.
  Restarting or registering the same content does not reopen the same decision.
* `managed_history()`, `rollback_managed()`, `managed_status()`, `managed_catalog()`, and
  `set_managed_tracking()` mirror the prompt API. Stopping tracking retains the
  library copy and survives subsequent automatic registration.

Skill proposal trees contain `name`, `directories` (path to true), and `files`.
Each file contains either UTF-8 `text` or binary `content_b64`, plus `executable`.
Disjoint text edits can merge into a proposal. Binary differences require a choice.
Edits to bytes and executable flags can be reconciled independently. Deleting an
edited file conflicts. Full metadata, portable paths, containment, collision, and
size checks still apply. No scripts execute during import, validation, or review.
Incomplete authoring buffers remain editor drafts until valid; the managed save API
does not activate invalid packages. Empty directories are preserved on file deletion.

Consumers/UIs must expose the returned pending status, for example: "Publisher update
needs review. Your customized version is still active." A pending update or notification
dismissal never changes active content. Sync errors are separate from pending updates.

State records live at `managed/PUBLISHER/SKILL.json`, immutable history at
`managed-history/PUBLISHER/SKILL/STATE_DIGEST.json`; package manifests and bytes reuse
`objects/` and `blobs/`. Mutations use the existing store lock. Validated immutable
content/history is written before the atomic state pointer. Orphaned content/history
from an interrupted publication does not become active.

## Migration and immutable release consumers

Existing source registrations remain filesystem authoring registrations. They are
not silently converted, and their editing/immutable-release contracts are unchanged.
Use an application handle or explicit ensure to adopt owned storage. `catalog()` adds
a `managed` list without replacing its local entries.

`resolve_managed`, `read_managed_file`, and `export_managed` accept
`revision=STATE_TOKEN` to pin historical accepted content. To supply an existing
Agent Manager release binding, use `release_managed(publisher, skill_id, version,
revision=None)`. It publishes the selected owned snapshot in a deterministic managed
source namespace and returns the existing `source/name@version` release contract.
Existing release labels remain immutable; publishing does not advance profile bindings.

Suite exposes a managed skill library with file editing, binary upload/download,
executable flags, update comparison and per-conflict choices. History/restore,
pause/resume publisher tracking and immutable publication use the same SDK contract.
WebSocket transfers are chunked; bounded `managed_view()` previews keep large assets
out of catalog/review responses. `managed_summaries()` supplies inexpensive pending
status for events, focus, reconnect and periodic visible-page refreshes.

`adopt_legacy(publisher, skill_id, legacy_path, publisher_path, ...)` preserves imported
bytes before switching a consumer. When no original publisher baseline is known,
`baseline_known=False` requires explicit choices for differences between imported and
publisher content. Successful reconciliation establishes the publisher baseline.
Stale reviews cannot apply, and newer updates retain the user's browser choices
until a fresh preview is validated. Existing Agent Manager bindings continue to
select explicit immutable releases.

## CLI

```sh
skill-manager --home /tmp/skills managed ensure pathspec review /path/to/code-review --publisher-revision 12
skill-manager --home /tmp/skills managed status pathspec review
skill-manager --home /tmp/skills managed read pathspec review SKILL.md
skill-manager --home /tmp/skills managed save pathspec review --files @changes.json --expected-revision STATE
skill-manager --home /tmp/skills managed preview pathspec review
skill-manager --home /tmp/skills managed apply pathspec review --resolutions @choices.json --expected-revision STATE
skill-manager --home /tmp/skills managed export pathspec review /existing/parent/code-review
skill-manager --home /tmp/skills managed release pathspec review --version v1
skill-manager --home /tmp/skills managed tracking pathspec review off --expected-revision STATE
```

CLI saves accept file specifications with `content_b64` and optional `executable`,
or null to delete a file. `managed list`, `history`, `resolve`, and `rollback` provide
the remaining inspection/history operations. Results are strict JSON; existing
structured CLI errors and exit-code conventions apply.


Direct creation, public GitHub sources and retained import jobs are documented in
[GitHub sources](github-sources.md). GitHub acquisition is an optional extra;
local creation/import and existing managed content do not need it.
