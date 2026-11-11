# Python SDK, version 1

`SDK_CONTRACT_VERSION == 1`. Public types and handles are exported from
`skill_manager`; installed distributions include `py.typed`. Existing Manager
methods, CLI commands, native store formats, histories, and release references
remain supported. The SDK uses Prompt Manager's public package lifecycle; it does
not implement another update or merge policy.

`manager.project(root)` also exposes project-scoped installation through the
public `Project` handle, with exported `ProjectPlan` and `ProjectOperation` JSON
contracts. See [project connections](projects.md) for collection import, previews,
application, local-edit retention, and explicit interruption recovery.

## Explicit identity and registration

```python
from skill_manager import Manager

manager = Manager(home="/absolute/skill-store")
skill = manager.skill("example-application", "review")
result = skill.register(
    "/installed/skills/code-review",
    publisher_revision=12,
    version="1.4.0",
)
effective = result.content
status = result.status
```

Constructing a handle validates its identity without reading, creating, or
refreshing content. Publisher and skill IDs are stable identifiers, independent
of installation paths and native directory names. Above, `review` can identify a
native `code-review` package. Exports still require its native name as the
destination basename.

`register` returns a `RegistrationResult` with a `ResolvedSkill` as `content` and
the matching captured `ManagedStatus` as `status`. The complete package is
effective content: instructions, scripts, text references, binary assets,
executable flags, hidden files, and empty directories. Nothing is rendered,
flattened into a prompt, or executed.

Registration policy:

| Publisher observation | Effective content |
| --- | --- |
| First valid registration | Owned verified snapshot |
| Repeated content | Existing accepted content, without an unnecessary revision |
| Newer ordered update, untouched copy | Validated incoming content |
| Update with user customizations or a rollback hold | Existing copy; pending reconciliation is exposed |
| Older ordered revision | Existing copy; `ignored_older` outcome |
| Invalid/unavailable source or conflicting reused revision | Verified existing copy with `refresh_failed` and `sync_error`; error if no usable copy exists |

Invalid publisher arguments/identities fail before ingestion. `refresh(source,
publisher_revision=..., version=...)` is the strict alternative: failed ingestion
raises rather than returning retained content. Publisher revisions order updates;
version strings are descriptive labels. Unordered changed publisher content needs
review. GitHub-backed skills use explicit `check_updates`, not bundled registration.

Accepted content and registration status are captured together through the
lifecycle SDK. A concurrent edit after registration cannot make the result's
content and state revision disagree. Return values expose pending updates and
sync errors separately; consumers should surface those diagnostics.

## Captured, offline resolutions

```python
resolved = skill.resolve()
instructions = resolved.read_file("SKILL.md")  # bytes
image = resolved.read_file("assets/example.png")
receipt = resolved.export("/existing/consumer/code-review")
release = resolved.release("v1")
```

`resolve()` reads accepted local content. It does not refresh publisher files,
fetch GitHub, accept a pending update, or execute scripts. `ResolvedSkill` exposes
`reference`, `name`, `digest`, `revision`, `status`, `manifest`, `metadata`, `files`,
and `directories`. Public dictionaries/lists are defensive copies.

`read_file`, `export`, and `release` on a resolved object select its exact
historical accepted revision, even if another process edits or reconciles the
skill later. Reads verify stored content and fail on missing/corrupt records;
there is no fallback to newer content. Export preserves native bytes/structure and
refuses conflicting destinations. Release labels remain immutable.

`skill.export(destination, revision=None)` captures and exports the selected state.
`skill.release(version, revision=None)` publishes the current or explicitly pinned
state. To use the same state inspected earlier, prefer the resolved object's
methods or pass its revision explicitly.

## Guarded authoring and candidate validation

```python
from skill_manager import FileContent

current = skill.resolve()
changes = {
    "references/checklist.md": FileContent(b"Check correctness and missing tests.\n"),
    "scripts/check.py": FileContent(script_bytes, executable=True),
    "references/obsolete.md": None,
}
candidate = skill.validate(changes, revision=current.revision)
saved = skill.save(changes, expected_revision=current.revision)
```

`FileContent` requires bytes and a boolean executable flag; text callers encode
UTF-8 explicitly. Existing `{"content": bytes, "executable": bool}` inputs also
work in the handle. A null entry deletes a file. When replacing an executable
file, pass its executable flag explicitly; the default is false.

Validation checks the complete resulting native skill package without saving it.
An invalid candidate raises `SkillError`. A valid candidate returns `valid`, the
observed state `revision`, and its proposed package `digest`. Saving requires the
observed revision; intervening edits or updates cause a conflict. For changes
computed from `current` above, save using `current.revision` so an intervening edit
cannot be overwritten; a later validation result alone does not prove the editor's
buffer was based on that revision.

Deleting files preserves existing empty directories. There is no new directory
editing API in version 1. Native metadata/path/containment/size/collision checks
remain enforced before activation. Exported directories are independent copies;
editing one does not automatically update managed content.

## Explicit whole-package reconciliation

```python
review = skill.preview_reconciliation()
accepted = skill.reconcile(
    expected_revision=review["revision"],
    resolutions={conflict_pointer: {"take": "managed"}},
)
```

Review compares baseline, accepted customizations, and incoming publisher content.
Disjoint text edits can merge into a proposal. Binary conflicts require choices;
executable changes participate independently. Deleting an edited file conflicts.
Proposals do not become active automatically when the skill is customized.

Choices accept `take` (`managed`, `publisher`, `baseline`), explicit `value`, or
`delete`. An entire side can be selected with `take="managed"` or
`take="publisher"` instead of resolutions. All conflicts must be resolved and the
complete proposal must validate. Stale review revisions fail. Keeping the user's
content advances the publisher baseline so the same update does not reopen review.

`inspect(review=True, ...)` supplies bounded comparisons. Ordinary `inspect()`
lists files; select a JSON pointer such as `/files/SKILL.md` with `offset`/`limit`
for a bounded text preview. Binary previews return size/hash instead of full data.
Conflict pointers escape slashes in filenames using JSON pointer rules.

## History, source tracking, and other operations

| Handle method | Behavior |
| --- | --- |
| `status(revision=None)` | Captured state and derived customization/update status |
| `history()` | Native immutable state history |
| `compare(before_revision, after_revision=None)` | Whole-package structural changes; omitted after means current accepted content |
| `rollback(revision, expected_revision=...)` | Restore historical content and hold it against automatic publisher advancement |
| `set_tracking(enabled, expected_revision=...)` | Guarded publisher tracking control |
| `check_updates(expected_revision=None, force=False)` | Explicit GitHub acquisition; optional GitHub extra required for fetching |
| `source_status()` | Stored source/check diagnostics without fetching |
| `source_settings(expected_revision=..., pinned=None, interval_hours=None, label=None)` | Existing guarded source settings |
| `switch_source(expected_revision=..., ref=..., track=None)` | Fetch/stage an explicit reference switch for review |

## References, serialization, and errors

```python
serialized = skill.reference.to_dict()
reopened = manager.open_skill(serialized)
same_content = reopened.resolve(revision=resolved.revision)
```

`SkillReference(store, publisher_id, skill_id)` has the closed
`skill-manager/skill-ref-1` JSON contract. A reference belongs to one exact store;
opening it through another store is a conflict. It records skill identity, not a
selected revision. Save the revision alongside it for historical resolution.
`RegistrationResult.to_dict()` and `ResolvedSkill.to_dict()` produce JSON-compatible
native records; they do not embed complete binary content. `FileContent.to_dict()`
uses base64 for portable serialization, while SDK saves use the original bytes.

SDK operations raise structured `SkillError`, including filesystem failures.
Manager methods retain their prior error boundaries. Contract version 1
adds APIs without migrating stores, removing Manager handles, or importing private
Prompt Manager modules. The existing application handle remains available; new
integrations should prefer explicit per-skill handles and registration calls.

The runnable [application example](../examples/sdk_application.py) demonstrates
publisher registration, a user customization, pending update, explicit acceptance,
and export of the resulting complete package.
