# Prompt Manager Python SDK

`SDK_CONTRACT_VERSION` is `1`. The synchronous SDK uses only Python's standard
library and the installed Prompt Manager package. Import supported entry points
and value types from `prompt_manager`.

## Create and use a personal prompt

```python
from prompt_manager import Manager, SDK_CONTRACT_VERSION

manager = Manager(home="/absolute/path/to/prompt-store", packs=[])
created = manager.create_managed("review", content="Review carefully.", role="system")
package = manager.package("local", "review")
result = package.render("review", format="messages")
print(result["output"])
```

Creation is create-only; repeating it with an existing ID raises a conflict.
`import_local(path, package_id=...)` imports a native pack directory or a portable
snapshot JSON file under publisher `local`. Its returned status identifies the
package in `id`. Supply a stable package ID if the source may move.

`Manager` construction and `package(publisher_id, package_id="default")` do not
register content, refresh a publisher, or create the store. A handle can target a
package that does not exist yet; reading it fails until content is registered.
Explicit `packs=[]` disables ambient working-directory draft discovery.

## Register and use publisher content

```python
package = manager.package("publisher", "main")
result = package.register(
    "/installed/tool/prompts", publisher_revision=12, version="1.4.0",
    prompt_id="review", inputs={"task": "Review the parser"},
)
use_prompt(result.content)
# Optional notification or logging:
print(result.status["outcome"], result.status["reconciliation_required"])
```

`register()` performs the existence/version check, publisher ingestion, effective
content selection, and rendering. The caller does not compare hashes or decide
whether to use user or publisher content. `RegistrationResult` has two fields:

- `.content`: effective rendered text, or role/content messages with
  `format="messages"`. Text retains the same role formatting as `render()`.
- `.status`: captured managed status, including `outcome`, content digest, state
  revision, pending review, and any `sync_error` diagnostic.

The source must contain a valid pack or complete snapshot. For a pack containing
one prompt, `prompt_id` can be omitted. Multi-prompt packs require an explicit
selection. Supply the inputs required by the effective prompt. Different inputs
can be rendered later with `package.render()` without re-registering.

| Outcome | Content returned |
| --- | --- |
| `imported` | First captured publisher content |
| `unchanged` | Current effective content, preserving user customizations |
| `updated` | Content selected by the existing ordered update policy |
| `update_pending` | Current effective content while incoming content awaits review |
| `ignored_older` | Current effective content; no publisher downgrade |
| `tracking_stopped` | Retained effective content; no publisher ingestion |
| `refresh_failed` | Verified existing content with an error diagnostic |

A newer ordered update can activate an unmodified package. User customizations
stay active until reconciliation is applied. Changed unordered content requires
review. A reused publisher revision with different content is a failed ingestion;
registration can still return verified existing content and a conflict diagnostic.

Content and status refer to one captured state. Later edits do not change an
already returned result; use another registration or render to observe them.
Publisher files are unnecessary for rendering after capture.

Registration raises if there is no usable accepted copy, accepted content fails
integrity checks, metadata/identity is invalid, prompt selection is ambiguous or
missing, or the effective prompt cannot render with the supplied inputs. Rendering
errors are not treated as refresh failures. Publisher ingestion can succeed even
when subsequent prompt selection/input validation fails; the combined operation
is not a rollback transaction around publisher ingestion.

The same operation is available as
`Manager.register_prompt(publisher_id, source, package_id=..., prompt_id=..., ...)`.
`RegistrationResult.to_dict()` returns a detached JSON-compatible value with schema
`prompt-manager/registration-result-1`, `content`, and `status`. It is a result
snapshot, not a live package reference or a complete render-provenance record.
`package.render()` retains the detailed existing render result when that is needed.

## Explicit refresh

`package.refresh(source, publisher_revision=..., version=...)` remains available
when callers only want strict ingestion without rendering. It raises on ingestion
failure even when an existing copy remains usable. Neither handle construction
nor `render()` refreshes a publisher implicitly.

`Package` operations normalize filesystem `OSError` to `PromptError(code="io")`;
existing `PromptError` codes are preserved. Programmer errors such as omitted
required arguments are ordinary Python errors. Catalog methods may report errors
per entry. Diagnostics are persisted when storage permits; after a storage-write
failure, a registration result may carry a diagnostic that could not be persisted.
Store/lock access failures that prevent safe capture still raise.

## Edit and reconcile

```python
observed = package.status()
candidate = {"sections/review.md": "Custom review instructions.\n"}
checked = package.validate(candidate)
saved = package.save(candidate, expected_revision=observed["revision"])
```

Files map normalized relative paths to text or `None` for deletion. Manifest and
asset changes can be saved together. The complete result and saved rendering
examples must validate before activation. Validation does not save content,
create objects, or advance history. Invalid candidates raise `PromptError`.
Its result records the observed `revision` and proposed content `digest`; a valid
preview does not reserve that revision. Save still requires a current guard.

After a refresh reports `reconciliation_required`:

```python
preview = package.preview_reconciliation()
# Review candidate, managed_changes, publisher_changes, and conflicts.
accepted = package.reconcile(
    expected_revision=preview["revision"], take="managed",
)
```

`take="managed"` acknowledges the publisher update while retaining user content;
`take="publisher"` accepts publisher content. Omit `take` for a clean proposed
merge or supply explicit conflict resolutions as described in
[managed reconciliation](MANAGED.md). Preview never activates content. A new edit
or incoming update invalidates an older apply token.

`inspect(review=True, ...)` provides bounded file/conflict inspection. Its `path`
is an inspection JSON pointer, for example `/files/body.md`; edit mappings instead
use relative file paths such as `body.md`. Inspection supports offsets and limits
and can require an observed revision.

## History and publication

```python
history = package.history()
old_revision = history[-1]["revision"]
pinned = package.render("review", revision=old_revision)
comparison = package.compare(old_revision, inputs={}, prompt_id="review")
release = package.release("approved-1", revision=old_revision)
```

`compare(before_revision, after_revision=None)` compares two states of the same
package. Omitted `after_revision` captures the current state. Supplying `inputs`
also compares rendered output using the same inputs. Cross-package or mixed
managed/draft selectors are not part of this operation.

`restore(revision, expected_revision=...)` creates a new state selecting historical
content while retaining publisher baseline/pending information. It does not erase
history. `set_tracking(enabled, expected_revision=...)` changes tracking without
refreshing. Saves, reconciliation, restoration, and tracking changes require an
explicit observed state revision. Releases are immutable and never advance
logical mappings. Pin a revision when publication must select a reviewed state.

## References and content export

```python
import json

serialized = json.dumps(package.reference.to_dict())
recovered = manager.open_package(json.loads(serialized))
exported = recovered.export("/exports/approved-prompts.json", revision=old_revision)
```

`PackageReference` is a frozen value with schema `prompt-manager/package-ref-1`,
canonical absolute store path, `publisher_id`, and `package_id`. `to_dict()` and
`from_dict()` validate/serialize that identity. `open_package()` accepts the value
or its dictionary and rejects another store. References follow the current package;
retain a separate state `revision` to pin content. They are not access credentials
or portable store backups. Serializing a handle itself is unsupported.

`export()` writes a complete snapshot including captured dependencies. Repeating
an identical export is idempotent; different existing content causes a conflict
instead of being overwritten. Import that JSON into another store with
`import_local()`. Export does not include managed history or logical mappings and
requires the same hard-link support as immutable publication.

## Sources and logical mappings

Source locations and logical mappings have public JSON result types. They remain
local descriptors; neither requires an application executable or a remote service.

```python
from prompt_manager import ApplicationDescriptor, SourceDescriptor

source = manager.register_source(
    "authoring", ["/absolute/path/to/pack"], expected_revision=None,
)
observed: SourceDescriptor = manager.source_descriptor("authoring")
application: ApplicationDescriptor = manager.register_application(
    "workspace", prompts={
        "review": {"kind": "managed", "publisher_id": "publisher",
                   "package_id": "main", "prompt": "review"},
    },
)
resolution = manager.resolve_application("workspace", "review")
rendered = manager.render_application("workspace", "review", {"task": "Review the parser"})
```

`source_descriptor(id)` and `application_descriptor(id)` read normalized
registrations and raise `not_found` if absent. They do not create storage or rewrite
source descriptors. `SourceDescriptor` always includes owner metadata, writable
policy, and an observed revision. Owner metadata can be any supported JSON value.

`register_source()` preserves its return shape: an unguarded upsert without
metadata returns only schema, ID, label, and paths. Its public return type therefore
allows `SourceRegistration` or `SourceDescriptor`. Guarded registration returns
normalized metadata; explicit `expected_revision=None` is create-only. Use the
normalized getter to obtain a revision reliably, including for stored records.
Source paths accept sequences of filesystem paths, excluding strings/bytes as the
container and paths containing NUL characters. `UNSET` preserves unguarded
upserts; prefer explicit guards for new mutations.

`ManagedTarget`, `ReleaseTarget`, and `DraftTarget` form the `PromptTarget` union.
Their `kind` fields distinguish managed content, immutable releases, and registered
external drafts. `PackagedFallback` describes a call-scoped fallback; a resolution
that uses it reports a `PackagedTarget`. `ApplicationResolution` retains nullable
fields where a source, catalog entry, path, or release reference does not apply.
The optional `managed` field is present only for managed selections.
`ApplicationRenderResult` carries that resolution alongside the existing render
fields. Guarded mapping changes/removal return `ApplicationDescriptor`; guarded
source removal returns the normalized `SourceDescriptor` it removed. Removing a
descriptor does not delete prompt content or releases.

`Manager.sources()` and `applications()` return descriptors or `RegistrationIssue`
entries. An issue retains its existing string `error` and adds
`diagnostic={"code": ..., "message": ...}` for machine-readable handling. A corrupt
entry does not hide healthy entries. Failure to access the listing itself raises.
Catalog application-resolution failures continue to use their structured `error`
object; these are a different result shape from descriptor-list issues.

Direct source and logical-mapping operations normalize filesystem `OSError` to
`PromptError(code="io")`, matching package handles. Existing domain errors such as
`conflict`, `not_found`, `json`, and `integrity` retain their codes. Programmer
exceptions are not broadly caught. Result schemas and successful CLI outputs remain
compatible; list issue diagnostics are additive and do not rewrite stored records.
Callers previously catching raw `OSError` from these Manager methods should now
catch `PromptError` and inspect its code. Type declarations and shared error handling
add no third-party runtime dependency.

## Store inspection

`manager.check_store(include_external=True)` returns a `StoreCheckResult` with
structured `StoreFinding` entries. It is read-only, creates no locks or absent
store, and distinguishes corruption/access errors from availability and artifact
warnings. It validates known reference roots, including historical pins and
digest-only logical mappings. `stable` describes before/after local fingerprints,
not an atomic snapshot; retry an observed changing check with writers idle.
See [storage reliability](storage-reliability.md) for details and CLI exit rules.

## Library organization

`package.rename(label, expected_revision=...)` changes only a display label;
`None` clears it. `archive()` and `unarchive()` require the same guarded state token
and control browsing visibility without changing tracking or reference resolution.
Managed list/catalog methods default to hiding archived packages and accept
`include_archived=True`. `package.duplicate(target_id, revision=..., label=...)`
creates a local, nontracking copy with a new pack name and independent history.
Original recipe IDs remain unchanged. All return `ManagedStatus`; optional `label`
and `archived` fields describe metadata when present. See [library management](library-management.md)
for detailed behavior and stored-reader compatibility.

## Tags and metadata search

`package.set_tags(tags, expected_revision=...)` replaces normalized tags through a
guarded state update. `manager.search_managed(query="", publisher_id=..., tags=...,
archived=False, customized=None, reconciliation_required=None)` returns the typed
`ManagedSearchResult` without reading prompt bodies. `archived=True` selects archived
packages and `None` includes both states. Query terms are casefolded substring
matches across accepted metadata; requested tags are all-required exact matches.

Missing descriptions are reported through `metadata_missing`, not loaded silently.
`package.refresh_metadata(expected_revision=...)` explicitly captures accepted
name/description metadata without refreshing a publisher. See [search and tags](search.md)
for result completeness, filter/error conventions, and stored compatibility.

## Store backup and restore

`manager.backup_store(destination)` returns `StoreBackupResult` after capturing and
validating all supported records. `Manager(home=new_path).restore_store(source)`
returns `StoreRestoreResult` after checksum and staged-store validation. The new
home must not exist, even as an empty directory. Existing stores are never merged.
Content/history revisions and mappings remain unchanged; external draft files are
excluded and source paths are preserved as metadata. See [backup and restore](backup-restore.md)
for examples, archive limits, warning semantics, and interruption behavior.

## Public contracts and compatibility

The root package exports `Manager`, `Package`, `PackageReference`, `PromptError`,
`SDK_CONTRACT_VERSION`, `RegistrationResult`, and the result types declared in
[contracts.py](../src/prompt_manager/interfaces/contracts.py).
`TypedDict` declarations describe JSON-compatible results; they do not create
wrapper objects or perform runtime validation. Consumers should allow additional
result fields. Installed wheels include `py.typed` for type-aware consumers.

The SDK retains existing status, render, and release shapes. New validation and
export results have `prompt-manager/managed-validation-1` and
`prompt-manager/managed-export-1` schemas. Comparison retains the existing diff
shape and adds state revisions to its side identities. History returns stored
state records; current status additionally derives customization and update flags.

Content digests establish snapshot equality. State `revision` strings guard writes
and pin history. Integer `publisher_revision` orders publisher updates; display
versions do not. These values are not interchangeable.

Existing `Manager` methods remain compatible. Prefer `application_descriptor(id)`
for logical descriptor lookup and `package(...)` for owned package operations.
The `application(..., bundled_pack=...)` overload registers during
construction and retains its failure fallback; it is outside the explicit
handle contract. Source and logical-selection Manager methods now normalize
filesystem failures as described above; other Manager methods retain their existing
exception behavior. Generic source and logical-selection APIs remain available; no agent,
provider, remote transport, or background runtime is introduced by the SDK.

## Managed CLI parity

The combined operation is also available through the CLI:

```sh
prompt-manager managed register publisher main /installed/tool/prompts --publisher-revision 12 --prompt review --inputs '{"task":"Review the parser"}'
```

It returns JSON with `schema`, `content`, and `status`, including usable-content
fallback on ingestion failure. The existing `managed ensure` command remains
strict and returns its original status shape.

The managed CLI uses package handles for identity-bound operations and preserves
existing JSON results. It also exposes the new bounded content operations:

```sh
prompt-manager managed validate publisher main --files @candidate.json
prompt-manager managed compare publisher main STATE --prompt review --inputs '{}'
prompt-manager managed export publisher main /exports/prompts.json --revision STATE
```

Managed JSON inputs accept inline JSON or `@FILE`. SDK contract tests live under
`tests/sdk/`; stored-content compatibility and isolated execution remain covered
by integration tests.
