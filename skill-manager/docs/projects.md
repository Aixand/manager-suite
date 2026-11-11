# Project skill connections

Project connections install explicitly selected accepted skill packages into a
repository/workspace directory and record their installed content identities.
Publisher-to-library reconciliation and library-to-project updates are distinct
decisions. No global agent configuration or running agent session is changed.

## Import a local collection

```sh
# Discovery only; the path may contain nested groups of native skill directories.
skill-manager import /path/to/bbb-skills

# Explicit selection, or --all after reviewing discovery.
skill-manager import /path/to/bbb-skills --select code-research --select pathspec/pathspec-execute
```

Discovery stops at each SKILL.md package, reports invalid packages and linked
paths, and skips common generated/VCS directories and `.skill-manager` controls.
Limits: 16 discovery levels, 10,000 examined directory entries, and 256 candidates.
Individual packages retain native limits. A selected import batch is bounded to
256 MiB raw file bytes and 10,000 package entries in aggregate. Select smaller
batches if necessary. A root that is itself a package uses selection `.`.

All selected packages are captured and validated before the first import. Pass
`--expected-revision` from discovery to reject changed collection previews.
Imports use the existing canonical-path identity scheme and are one-time local
copies, without ongoing publisher tracking. Repeated imports preserve managed
edits. Store publication is per package; an IO failure can retain earlier completed
imports, and repeating the same selection does not overwrite them. No scripts run.

## Connect a project

```sh
skill-manager connect code-research --project /path/to/repository --skills-dir skills
skill-manager connections --project /path/to/repository
skill-manager update-connections --project /path/to/repository
skill-manager disconnect code-research --project /path/to/repository
```

`--project` defaults to the current directory, without searching for a Git root.
`--skills-dir` is required for the initial connection and is then recorded. It
must be a portable relative path inside that project, outside manager and VCS
control directories. No agent-specific directory is guessed. You can explicitly
choose a directory such as `.agents/skills` when that is what your consumer needs.

Selectors accept `publisher/skill-id`, an unambiguous skill ID, or an unambiguous
native package name. Ambiguities require a qualified selector. Packages retain
their native names as destination basenames; collisions fail before publication.
At most 256 skills can be connected to a project. Connecting an already recorded
skill retains its installed revision; explicit updates select newer accepted data.

SDK connection selectors also accept `Skill`, `ResolvedSkill`, `SkillReference`,
or its `to_dict()` representation, consistently for connect, update, disconnect,
and saving a variant. References must belong to the same manager store. A resolved
handle supplies the skill identity; connect selects its current accepted library
content when previewing, then pins that revision in the plan. Passing an old
resolved handle does not request a historical installation.

Omitting selectors from `preview_update()` updates all recorded connections.
Disconnect always requires an explicit nonempty selection; `None` does not mean
disconnect everything. Collection selections instead use discovered relative paths.
The CLI discovery-only form rejects `--expected-revision`; use that guard with
`--select` or `--all` when importing.

Existing unrelated destinations are conflicts. `--adopt` explicitly claims only
an identical existing package; it does not overwrite different content. Repeated
application of the same sealed plan returns its original operation receipt.

## Review and apply guarded plans

```sh
skill-manager connect local/review --project . --skills-dir skills --preview > plan.json
skill-manager apply-project-plan --project . --plan @plan.json

skill-manager update-connections --project . --preview
skill-manager disconnect local/review --project . --preview
```

SDK equivalent:

```python
project = manager.project("/path/to/repository")
plan = project.preview_connect([skill.reference], skills_dir="skills")
receipt = project.connect(plan)

updates = project.preview_update()
project.apply_update(updates)
status = project.connections()
```

The CLI and SDK use these names for the same operations:

| CLI | SDK |
| --- | --- |
| `import PATH` | `manager.discover_collection(path)` → `CollectionDiscovery` |
| `import PATH --select ...` / `--all` | `manager.import_collection(...)` → `CollectionImport` |
| `connect ... --preview` | `project.preview_connect(...)` → `ProjectPlan` |
| `update-connections ... --preview` | `project.preview_update(...)` → `ProjectPlan` |
| `disconnect ... --preview` | `project.preview_disconnect(...)` → `ProjectPlan` |
| `restore-connections --preview` | `project.preview_restore(...)` → `ProjectPlan` |
| `apply-project-plan --plan @FILE` | `project.apply(plan)` → `ProjectOperation` |
| `connections` | `project.connections()` → `ProjectConnections` |
| `project-operations` | `project.operations()` → `list[ProjectOperation]` |
| `recover-project ID --action ...` | `project.recover(id, action=...)` → `ProjectOperation` |
| `save-project-copy ... --as ID` | `project.save_variant(selection, id, ...)` → `ManagedStatus` |

Without `--preview`, mutation commands preview and apply immediately. In the SDK,
`connect(plan)` and `apply_update(plan)` check the plan action before applying;
`apply(plan)` accepts any project plan. `disconnect(selections)` is a convenience
that previews and applies, while restoration uses `apply(preview_restore(...))`.
All result contracts are exported from `skill_manager` and remain JSON mappings.
Collection candidates can report structured validation diagnostics or a text
filesystem error; invalid candidates are visible and cannot be imported.

CLI failures use the `skill-manager/error-1` JSON envelope on stderr. SDK failures
raise `SkillError` with the same diagnostic fields. Validation, missing identities,
and conflicts exit with 2; unsupported operations exit with 3; uncertain operations
exit with 4 and provide the operation ID and recovery guidance when available.
Other operational failures exit with 1. See the recovery section before retrying
an interrupted mutation.

`ProjectPlan` and `ProjectOperation` are exported JSON contracts. Treat plans as
opaque: serialize all fields unchanged. Previews persist immutable plan records in
the selected manager store, but do not write project files. Applying checks the
sealed plan, project/store identity, receipt revision, control files, and complete
destination observations. Altered or stale plans fail. Plans pin exact selected
accepted content; later library changes do not substitute a different package.

The CLI calls these public SDK/Manager APIs. Directory export remains available for
untracked one-time copies.

## Project records and portability

`.skill-manager/project.json` is the portable manifest: native names, publisher and
skill IDs, selected managed revisions, content digests, and a relative destination.
It contains no absolute library-home path. You may commit this file deliberately.

Local receipts/plans/operation journals live under
`MANAGER_HOME/projects/HASH_OF_CANONICAL_PROJECT_PATH/`. Separate same-named
directories and worktrees therefore have distinct installations.

Project-local `local.json`, `pending.json`, `connection.lock`, and `operations/`
are local ownership, coordination, staging, and recovery data. Keep them out of
version control. Skill Manager does not edit `.gitignore` automatically. Decide
separately whether deployed skill directories should be committed or ignored.

```sh
# Apply a committed manifest using content already available in the local library.
skill-manager restore-connections --project . --preview
skill-manager restore-connections --project .
```

Restoration resolves the recorded identity and exact digest locally. A matching
current state can supply content if the original state token is unavailable in
that store. Missing content fails with an actionable error; nothing is fetched.
Use consistent publisher/skill IDs across machines. Path-derived local import IDs
are not automatically remapped when the source collection moves.

Restoration can also repair missing owned packages or rebuild a missing manifest
from an existing local receipt, using pinned content rather than the latest
library version. Existing directories need explicit identical `--adopt`. Taking
ownership from another local store also requires explicit adoption; the previous
store then refuses further project mutations. Portable manifest edits must be
reviewed through restore, rather than overwritten by an unrelated update.

## Preserve project-specific edits

Status distinguishes installed, modified, and missing packages, plus available
accepted-library updates, pending publisher reconciliation, and library errors.
Inspection compares whole packages: bytes, executable flags, hidden files,
directories, and unexpected additions. Status supplies the observed destination
digest and the project receipt revision for guarded saves.

Updates and disconnects stop on modified content. To retain a project copy as an
independent local variant:

```sh
skill-manager save-project-copy local/review --as my-project-review --project . \
  --expected-revision PROJECT_RECEIPT_REVISION --expected-digest OBSERVED_PACKAGE_DIGEST
skill-manager disconnect local/review --project . --forget
skill-manager connect local/my-project-review --project . --adopt
```

SDK: `project.save_variant(selection, variant_id, expected_revision=...,
expected_digest=...)`, then `project.disconnect([selection], forget=True)` and
explicit adoption of the variant. The variant must be a valid whole skill and has
independent history with tracking disabled. Saving it does not update the shared
original or automatically rebind the project. Its native package name stays the
same, so forget the old identity before reconnecting the variant at that name.

`disconnect --forget` drops ownership while leaving files in place. Ordinary
disconnect removes only unchanged owned packages from the destination, retaining
their bytes in recovery storage. Other skills and unrelated files are preserved.
There is no automatic bidirectional merge of project edits in this version.

## Failure and recovery

All desired packages are staged and validated before changing destinations.
Updates use atomic no-replace directory moves: the old directory is retained,
checked again, and the desired package is published into the absent destination.
There is no overwrite/copy fallback if the filesystem cannot support those moves.
An update may briefly leave its target absent between moves; it is not a live
agent reload protocol. There is no transaction spanning multiple skill directories.

Journals retain per-skill progress and stage directory identities. A competing
directory, even with identical content, is not silently claimed or removed.
Pending project operations block new mutations across stores. An interrupted
operation raises a structured uncertain error with its operation ID.

```sh
skill-manager project-operations --project .
skill-manager recover-project OPERATION_ID --project . --action resume
skill-manager recover-project OPERATION_ID --project . --action rollback
```

SDK: `project.operations()` and `project.recover(id, action="resume"|"rollback")`.
Only explicit recovery resumes work. Resume verifies retained identities and
content before continuing. Rollback restores retained original directories and
moves newly published unchanged packages into retained storage; it never discards
project edits. After metadata publication starts, resume is required instead of
rollback. Competing/newly edited content can require manual review. Incomplete
stages are retained before rebuilding. Operations lists show the latest 100
receipts; an older known ID can still be recovered directly.

Backups, rolled-back packages, incomplete stages, and immutable plans are not
automatically garbage-collected. Keep pending recovery data intact. Full power-loss
durability and hostile-writer exclusion are not claimed; cooperating writers use
the store lock and project lock, and noncooperating changes are rechecked before
publication wherever observable. Windows-specific qualification remains separate.
