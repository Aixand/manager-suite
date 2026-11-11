# Skill Manager

For automatic imports into manager-owned storage, publisher updates, user edits,
and reconciliation, use [managed registrations](docs/managed.md). Existing source
registration and draft APIs below retain their filesystem-authoring behavior.

Development and installation work from this directory independently of the
Manager Suite checkout. See [standalone development](docs/development.md).

Standalone local management of native `SKILL.md` directories. Installable as
`aixand-skill-manager==0.1.0b0`; import `skill_manager`; run `skill-manager` or
`python -m skill_manager`. Python 3.11+ and PyYAML 6.0.3–6.x are required. This MVP
uses descriptor-relative access and locks on POSIX; atomic directory commits use
Linux `renameat2(RENAME_NOREPLACE)` or macOS `renameatx_np(RENAME_EXCL)` with
filesystem support. Unsupported environments
receive a structured error before directory publication.

Skill Manager registers explicit sources, inspects and edits drafts, validates
native metadata, publishes whole-directory snapshots, compares versions, and
exports to an explicit consumer directory. It works independently of Agent Manager,
Suite, provider credentials, and model services.

Accepted-content updates, reconciliation, and history use the public package
lifecycle SDK from `aixand-prompt-manager>=0.1.0b0,<0.2`. The dependency is one-way:
Prompt Manager remains independently usable and does not import Skill Manager.
Native skill validation, manifests/blobs, release references, and filesystem
export remain owned here. Existing stores require no migration. See
[the integration contract](docs/prompt-manager-integration.md).

GitHub acquisition is optional: install `aixand-skill-manager[github]` to use the
standalone `aixand-lib-github-source` library. Creation, local imports, stored skills,
history, and releases work without it. Skill Manager owns its acquisition policy
and retained jobs. See
[GitHub sources](docs/github-sources.md).

Store locking and Windows pinned-path access use the standalone
`aixand-lib-filesystem` library. The `.locks/store.lock` path and locking
protocol coordinate concurrent store users.

## Install and verify

```sh
python3 -m venv /tmp/skill-manager-env
/tmp/skill-manager-env/bin/python -m pip install .
/tmp/skill-manager-env/bin/skill-manager --help
/tmp/skill-manager-env/bin/python -B -m unittest discover -s tests -v
```

Use a temporary home during experiments. Selection order is explicit `Manager(home)`
or `--home`, then `SKILL_MANAGER_HOME`, then
`${XDG_DATA_HOME:-~/.local/share}/aixand/manager/skill`. Construction, listing an absent
store, and inspection do not create the store. Registration persists paths and
ownership metadata without copying source files. Sources default to read-only.

## Public library

The [version 1 Python SDK](docs/python-sdk.md) provides explicit per-skill handles,
registration returning effective content/status, and revision-pinned resolutions.
The [project connection workflow](docs/projects.md) adds nested collection import,
explicit project skill directories, guarded updates, project variants, and recovery.
The existing record APIs below retain JSON-compatible mapping/list results;
file reads return original bytes. `SkillError(code, message, *, uncertain=False,
operation_id=None, recovery=None)` exposes those attributes and `to_dict()`.

```python
from pathlib import Path
from skill_manager import Manager

m = Manager("/tmp/skill-store")
root = Path("/tmp/my-skills")
root.mkdir(exist_ok=True)
m.register_source("local", [root], writable=True, owner={"kind": "local"})
entry = m.create_draft("local", "review-notes", {
    "SKILL.md": {"content": b"---\nname: review-notes\ndescription: Review notes when preparing a meeting.\n---\nRead the supplied notes.\n",
                 "executable": False},
    "references/checklist.md": {"content": b"Check dates and unresolved actions.\n",
                                "executable": False},
})
edited = m.save_file(entry["id"], "references/checklist.md",
                     b"Check dates, owners, and unresolved actions.\n",
                     expected_digest=entry["digest"])
assert m.validate(edited["id"])["valid"]
release = m.release(edited["id"], "v1", expected_digest=edited["digest"])
resolved = m.resolve(release["reference"])
receipt = m.export(resolved["reference"], "/tmp/review-notes")
assert receipt["digest"] == release["digest"]
```

| Method | Arguments and behavior |
| --- | --- |
| `register_source` | `(identifier, paths, *, label=None, writable=False, owner=None, expected_revision=None)`; create-only with null guard; existing registration needs its observed revision. |
| `source`, `sources` | `(identifier)` / `()`; include location availability; corrupt registrations remain isolated in lists. |
| `unregister_source` | `(identifier, *, expected_revision)`; removes registration, retains files and releases. |
| `catalog` | `()`; source descriptors and source-scoped draft entries, with isolated diagnostics. |
| `show`, `read_file`, `validate` | `(entry_id_or_release)` / `(entry_id_or_release, relative_path)` / `(entry_id_or_release)`; inspection, bytes, and validation. |
| `create_draft` | `(source_id, name, files, *, path=None)`; each file supplies `content: bytes`, `executable: bool`; `path` selects a registered root when several exist. |
| `save_file` | `(entry_id, relative_path, content, *, executable=False, expected_digest)`; add/replace one file under a complete-draft guard. |
| `release` | `(entry_id, version, *, expected_digest)`; explicit publication; immutable label. |
| `resolve` | `(reference)`; validates captured manifest and blobs, independent of sources. |
| `releases` | `(source_id=None, name=None)`; retained versions, including removed sources, with isolated errors. |
| `diff` | `(before, after)`; draft IDs or release references; all file, permission, directory, and metadata changes. |
| `export` | `(reference, destination)`; absent package directory or identical existing package. |

Select drafts by the catalog `id`; names alone are ambiguous. Entry identity includes
the store, source ID, and canonical absolute package directory. Discovery checks a
registered root containing `SKILL.md`, otherwise its immediate directory children
containing `SKILL.md`. It never recursively searches projects. Missing registrations
and unavailable roots remain visible. Pointing at an existing skill supports direct
inspection; draft creation requires a collection root.

For an explicit fork of a plugin skill, read its files through `read_file`, retain
the manifest executable flags, and pass them to `create_draft` in a writable source.
Create any empty supporting directories separately; inspection and release capture
them. Ordinary catalog, inspection, and validation do not edit plugin caches.

Invalid frontmatter remains editable with `digest_kind="draft-files"`; the repair
digest covers all raw files and directories. Complete valid drafts use
`digest_kind="package"`. Publication requires complete metadata and containment
validation. Filesystem hazards without a safe digest require explicit filesystem
repair (for example, removing a symlink) before guarded editing. Deleting `SKILL.md`
removes an external directory from bounded discovery; restore it directly before
selecting the draft again. Existing published releases remain independent.

## CLI

Results are strict JSON by default; `--json` is accepted at each command. Errors
are JSON on stderr. Exit codes: 0 success, 2 validation/conflict/not-found or an
invalid validation result, 3 unsupported, 4 uncertain, 1 other operational failure.
Argument syntax errors are structured validation errors with exit 2. JSON inputs accept inline JSON or
`@PATH`; `none` is the explicit null revision token.

```sh
skill-manager --home /tmp/skill-store source register examples ./examples --json
skill-manager --home /tmp/skill-store source list --json
skill-manager --home /tmp/skill-store source show examples --json
skill-manager --home /tmp/skill-store catalog --json
# Copy the entry id and digest from the catalog result:
skill-manager --home /tmp/skill-store show ENTRY --json
skill-manager --home /tmp/skill-store show ENTRY --file assets/badge.png --json
skill-manager --home /tmp/skill-store validate ENTRY --json
skill-manager --home /tmp/skill-store release ENTRY --version v1 --expected-digest DIGEST --json
skill-manager --home /tmp/skill-store releases --source examples --name csv-audit --json
skill-manager --home /tmp/skill-store resolve examples/csv-audit@v1 --json
skill-manager --home /tmp/skill-store diff examples/csv-audit@v1 ENTRY --json
skill-manager --home /tmp/skill-store export examples/csv-audit@v1 /tmp/csv-audit --json
```

Writable authoring also supports `draft create SOURCE NAME --files @files.json`
with `{ "SKILL.md": { "content_b64": "...", "executable": false } }`, and
`draft save ENTRY RELATIVE --file PATH --expected-digest DIGEST [--executable]`.
Remove a registration with `source unregister ID --expected-revision REV`.
Replacing a source registration requires that same guard; `--writable` is explicit
on each replacement.

## Format, release, and export contract

Native frontmatter follows the [Agent Skills specification](https://agentskills.io/specification).
Required fields are `name` and `description`; optional fields are `license`,
`compatibility`, string-to-string `metadata`, and string `allowed-tools`. The name
matches the package basename. Safe YAML rejects duplicate keys, custom object tags,
aliases, and anchors. Unknown fields belong inside `metadata`. These restrictions
keep validation deterministic and bounded. Instructions and supporting content
remain original bytes; validation never executes scripts or calls a model.

All symlinks and special files are rejected, including internal symlinks and resource
root ancestors that are symlinks. No-follow descriptor-relative reads recheck types
and containment. Portable paths reject absolute/drive paths, backslashes, traversal,
control characters, Windows reserved components, trailing dots/spaces, and case or
Unicode normalization collisions. Hidden regular files and all supporting directories,
including empty directories, are included.

The `skill-manager/package-1` manifest contains exactly `schema`, `name`, `metadata`,
sorted `directories`, and sorted `files`. Each file records `path`, raw byte `size`,
raw-byte `digest`, and `executable`. Any native execute bit means executable; exports
use 0755/0644 files and 0755 directories. Timestamps, ownership, and other permission
bits do not affect the identity. The package digest hashes the entire strict canonical
UTF-8 JSON manifest. Script-only, asset-only, executable-only, and directory-only
changes therefore change its identity. See [storage details](docs/storage.md).

Release references are `source/name@version` or `source/name@sha256:HEX`.
Resolve an older label explicitly to select its previous content. A label can never
be rebound. Repeating publication with the same label/content returns the original
record. Digest references require an existing publication in that source/name
namespace and return the lexicographically first matching label's captured
publication provenance. Source unregister/removal/reuse cannot rewrite that record.

Export requires an existing caller-owned parent and a destination basename equal to
the recorded name. The complete staged sibling package is verified before atomic
no-replace publication. Different existing content is a conflict; there is no merge
or overwrite. An identical existing package succeeds with `status="identical"`.
Failure preserves previous destination content, including a competing empty directory.

Limits: 4096 file/directory entries, 64 MiB per file, 256 MiB total bytes, 32 path
components, 4096 UTF-8 path bytes, 255 bytes per component, 1 MiB `SKILL.md` metadata
inspection, and 64 MiB per stored JSON record. Exceeding a limit gives an actionable
diagnostic. Captures hold bytes in memory. These are local MVP limits, not additional
requirements of the Agent Skills specification.

Store mutations use one advisory lock per canonical store, serializing revision
comparison and commit. Directory publication also rejects noncooperating competing
destinations. Source writers outside Skill Manager are detected through capture
checks; they do not participate in its advisory lock. Files are fsynced before
commit; full power-loss durability, hostile-writer synchronization across stores,
and automatic orphan-blob/staging garbage collection are outside this MVP.

The [CSV audit example](examples/csv-audit/SKILL.md) includes a script, reference,
CSV, binary PNG, hidden settings, and an empty supporting directory. Provider-native
activation and managed profile assignments belong to Agent Manager. No skill
flattening into developer instructions, remote synchronization, marketplace,
cloud hosting, or plugin-manager implementation is included.

## Local developer commands

Inside the manager-suites source checkout, run from this directory with
Python 3.11+ and uv:

```sh
./dev.sh develop --local
./dev.sh check
./dev.sh test
./dev.sh build
```

Use `.\dev.ps1` on Windows. Each package owns its development environment and
helper; these commands do not invoke suite-wide checks. See the
[development guide](docs/development.md) for wheel-based setup without sibling
sources, lock updates, build outputs, and the separate suite release lifecycle.

### Default storage location

The default product directory is `${XDG_DATA_HOME:-~/.local/share}/aixand/manager/skill`.
Set `AIXAND_MANAGER_ROOT=/custom/managers` to use `/custom/managers/skill`
without changing the data location of unrelated applications. Unset, empty, or
relative `XDG_DATA_HOME` values fall back to `~/.local/share`.
Explicit CLI/SDK paths and product-specific environment overrides take precedence;
these paths are used directly. Existing configured storage paths remain supported.
Agent and Conversation config overrides remain separate from their state-home overrides.
Default path resolution creates no directories and does not move or adopt old stores.
To keep using an existing store, supply its original path explicitly. Native provider
homes connected by the user and operating-system temporary files retain their locations.
