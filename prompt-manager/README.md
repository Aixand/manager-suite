# Prompt Manager

A standalone Python library and CLI for owning, editing, composing, rendering, and
versioning prompts. Requires Python 3.11+ and has no third-party runtime dependencies.
No other application, service, network connection, or model is needed for local use.

See the [architecture](docs/architecture.md),
[managed lifecycle](docs/MANAGED.md), and [pack format](docs/FORMAT.md).
The [feature and API inventory](docs/SDK_INVENTORY.md) maps current coverage and
SDK coverage and remaining feature work. See the [version 1 Python SDK](docs/python-sdk.md)
for explicit package handles, public types, and compatibility rules.
The [package lifecycle SDK](docs/package-lifecycle.md) also exposes the shared
update/reconciliation engine to dependent native-package adapters, without adding
dependencies or changing the prompt-pack format.

## Install and create

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
export PROMPT_MANAGER_HOME="$PWD/.prompt-manager"
prompt-manager managed create review --content 'Review the supplied work carefully.'
prompt-manager managed render local review review
```

Build tooling uses setuptools; installed runtime operations use only the standard
library. On Windows, activate `.venv\Scripts\Activate.ps1` and set
`$env:PROMPT_MANAGER_HOME` instead.

```python
from prompt_manager import Manager

manager = Manager(home="/absolute/path/to/store", packs=[])
created = manager.create_managed("review", content="Review carefully.", role="system")
result = manager.render_managed("local", "review", "review", format="messages")
```

Creation is create-only. To change accepted content, use `save_managed()` with the
observed state revision. Local operations run synchronously without import jobs.

## Owned imports and publisher updates

```python
status = manager.import_local("/absolute/path/to/pack", package_id="my-prompts")
prompts = manager.package("publisher", "default")
result = prompts.register(
    "/absolute/path/to/bundled-pack", publisher_revision=12, version="1.4.0",
    prompt_id="review", inputs={"task": "Review the parser"},
)
content = result.content  # Effective prompt to use
status = result.status   # Registration/update outcome and diagnostics
```

Local import and registration capture complete snapshots with embedded dependencies.
`register()` checks for existing content, applies update policy, and returns the
effective rendered prompt with its captured status. A failed ingestion retains a
verified usable copy; no usable copy or invalid rendering still raises.
Strict ingestion without rendering remains available through `refresh()` and
`ensure_registered()`. The original files
are unnecessary after import. A native pack directory contains `pack.json` and its
referenced text assets. A self-contained snapshot JSON file is also accepted.
`resolve_managed(...)["snapshot"]` provides that portable JSON representation.

Local imports use publisher `local`. Without `package_id`, their identifier is
computed from the canonical source path; supply an explicit identifier to preserve
identity when moving a source. Changed local imports are unordered updates and
require review. Repeated imports preserve user edits.

Ordered publisher updates activate automatically only when the accepted copy is
unmodified. User customizations remain active until reconciliation is applied.
Hashes establish equality; `publisher_revision` orders updates; `version` is a
display label. Reused revisions with different content fail. Older revisions are
ignored. See [managed registrations](docs/MANAGED.md) for review, history, and pins.

## Packs, templates, and releases

Packs support typed inputs, defaults, reusable sections, literal documents,
conditional composition, text/messages output, and saved rendering examples.

```sh
prompt-manager release ./packs/common --version 1
prompt-manager validate ./packs/demo
prompt-manager render ./packs/demo --prompt review --inputs examples/inputs.json
prompt-manager release ./packs/demo --version 1
prompt-manager render demo@1/review --inputs examples/inputs.json --json
prompt-manager diff demo@1 ./packs/demo --prompt review --inputs examples/inputs.json
```

A release captures the whole pack and transitive dependencies. Published labels
are immutable; identical publication is idempotent. Release rendering does not
read original source directories. Digests identify content and are not signatures.
Rendering examples test composition and expected output, not model quality.

## External drafts and logical selections

External drafts are explicitly file-backed authoring locations. Unlike owned
imports, they require their source files to remain available.

```python
from pathlib import Path

pack = Path("/absolute/path/to/pack").resolve()
source = manager.register_source("authoring", [pack], expected_revision=None)
manager.register_application("workspace", prompts={
    "review": {"kind": "managed", "publisher_id": "local",
               "package_id": "review", "prompt": "review"},
})
result = manager.render_application("workspace", "review")
```

Use `source_descriptor(id)` and `application_descriptor(id)` for read-only normalized
lookups. Source and logical-mapping operations expose typed JSON contracts and
raise `PromptError` for filesystem failures. Broken entries in `sources()` or
`applications()` retain string errors and add structured diagnostics.

Logical mappings are generic local descriptors, with no provider or launcher
assumptions. Targets can select managed content (optionally pinned by state
revision), immutable releases, or registered drafts. Guarded mapping updates,
draft edits, and publication are separate operations. Missing mappings fail by
default. Explicit call-scoped packaged fallbacks remain supported; invalid
configured targets never silently fall back.

`catalog()`, `show_catalog()`, `save_catalog_file()` and `release_catalog()` expose
inspection and guarded draft authoring. Read-only source policy applies to catalog
edits, not direct filesystem access. Registration mutations accept an observed
`expected_revision`; explicit `None` means create-only. Unguarded source upserts
without a guard remain available via the exported `UNSET` sentinel.

## Storage and errors

Store selection: explicit `home`, then `PROMPT_MANAGER_HOME`, then
`$AIXAND_MANAGER_ROOT/prompt`, then `${XDG_DATA_HOME:-~/.local/share}/aixand/manager/prompt`.
Draft discovery uses explicit `packs`, `PROMPT_MANAGER_PACKS`, or `./packs`.
Pass `packs=[]` to disable working-directory discovery.

Content objects, releases, accepted managed state, immutable history, source
locations, and logical mappings live under the selected home. Reads verify content
integrity. Inspection does not create an absent store. Guarded mutations reject
stale state. Immutable publication requires filesystem hard-link support.

The library raises `PromptError` with a `code`. The CLI returns JSON for inspection
and mutations, raw text for ordinary renders, and structured provenance with
`--json`. Operational errors produce JSON on stderr and exit 1; usage errors exit 2.
CLI global options precede the subcommand. JSON mutation inputs accept inline JSON
or `@FILE`; direct `render --inputs` takes a file path.

## Standalone boundary and compatibility

The core owns prompt content, input contracts, update decisions, reconciliation,
and rendering. Model execution, remote repository discovery/synchronization,
background import jobs, provider setup, application startup, and tool-specific
migration policy are outside this package.

The catalog exposes owned content, releases, managed history, draft registrations,
and logical mappings. Source metadata records provenance without initiating remote
checks. `adopt_legacy()` preserves custom content with an unknown publisher
ancestor. Consumer setup, application startup, and background jobs belong to the
integrating application. The SDK provides explicit package handles alongside
Manager operations.

## Store inspection

```sh
prompt-manager --home /path/to/store store check
```

`Manager.check_store()` returns the same typed read-only report. It distinguishes
corruption/access failures from external availability warnings and unreferenced
artifacts. It never repairs or deletes content and does not create an absent store.
The CLI exits 1 for an unhealthy, incomplete, or changing local observation;
warnings alone exit 0. See [storage reliability](docs/storage-reliability.md) for
reference tracing, retry semantics, and platform limits.

## Full store backup and restore

```sh
prompt-manager --home /path/to/store store backup /backups/prompts.zip
prompt-manager --home /path/to/new-store store restore /backups/prompts.zip
```

`Manager.backup_store(destination)` and `Manager.restore_store(source)` provide
matching typed SDK results. Restore targets that Manager's home, which must not
exist. Archives preserve owned records, history, releases, and mappings and are
validated before publication. External draft files are excluded; their metadata
and paths remain unchanged. See [backup and restore](docs/backup-restore.md) for
archive limits, inclusion rules, and recovery behavior.

## Library organization

Managed packages support `rename(label, expected_revision=...)`,
`archive(expected_revision=...)`, `unarchive(expected_revision=...)`, and
`duplicate(target_id, revision=..., label=...)`. Display names do not change IDs
or content. Archival hides managed browsing entries while references and tracking
remain active; use `include_archived=True` or CLI `--include-archived` to list them.
Copies are independent personal packages with their own pack name/history and
tracking disabled. See [library management](docs/library-management.md) for examples,
guards, release naming, and stored-reader compatibility.

## Tags and metadata search

```sh
prompt-manager managed set-tags publisher main --tag review --tag python --expected-revision STATE
prompt-manager managed search 'parser review' --tag python --pending-review
```

`Package.set_tags()` provides guarded replacement updates. `Manager.search_managed()`
searches labels, IDs, accepted pack names/descriptions, and tags, with publisher and
archival/customization/review filters. It reads state metadata only, without loading
prompt bodies or maintaining an index. Records without descriptive metadata
are reported as incomplete and can be explicitly refreshed. See [search and tags](docs/search.md)
for matching rules, SDK types, CLI filters, and compatibility.

## Development

Use the [local development guide](docs/development.md) for pinned setup,
dependency/static checks, behavioral tests, and clean wheel/source builds.
Tests cover copied-package execution without site packages or network access,
and existing stored-content compatibility. Managed release installation remains
a separate suite operation.

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

The default product directory is `${XDG_DATA_HOME:-~/.local/share}/aixand/manager/prompt`.
Set `AIXAND_MANAGER_ROOT=/custom/managers` to use `/custom/managers/prompt`
without changing the data location of unrelated applications. Unset, empty, or
relative `XDG_DATA_HOME` values fall back to `~/.local/share`.
Explicit CLI/SDK paths and product-specific environment overrides take precedence;
these paths are used directly. Existing configured storage paths remain supported.
Agent and Conversation config overrides remain separate from their state-home overrides.
Default path resolution creates no directories and does not move or adopt old stores.
To keep using an existing store, supply its original path explicitly. Native provider
homes connected by the user and operating-system temporary files retain their locations.
