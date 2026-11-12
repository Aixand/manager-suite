# MCP Manager

MCP Manager can be developed and installed independently of the Manager Suite
checkout. Python 3.11+ and the declared `aixand-lib-filesystem` and `aixand-lib-github-source`
utility dependencies are required; no other manager, Runner, Carrier, or Suite package is required.

See the [local development guide](docs/development.md) for a private contributor
environment, pinned dependencies, checks, tests, and clean wheel/source builds.
`dev.sh`, `dev.ps1`, and `dev.py` run the same package-local workflow.

See [role, current features, and future direction](docs/DISCUSSION.md) for the
product discussion and proposed roadmap.

`aixand-mcp-manager==0.1.0b0` is an independent Python 3.11+ library and CLI for
stdio MCP definitions, Sonar/Lore preparation, retained generations/jobs,
freshness checks and injected runtime connectors. It owns source discovery, imports, tracking, installation generations, and their
retained jobs. GitHub acquisition and file locks use the independently installable
utilities in `shared/`; the repository layout is not required at runtime.

```sh
python -m pip install ./mcp-manager
mcp-manager --help
mcp-manager --home /tmp/my-mcps catalog --json
mcp-manager --home /tmp/my-mcps add docs --definition '{"command":"python3","args":["/path/to/server.py"],"env_vars":["DOCS_TOKEN"]}' --json
mcp-manager --home /tmp/my-mcps resolve --servers '["docs"]' --cwd /path/to/project --json
```

`python -m mcp_manager` exposes the same commands. JSON arguments accept inline
JSON or `@PATH`; duplicate keys, nonfinite numbers and invalid Unicode are rejected.
`--home` precedes the command; `--json` follows the leaf command. Output is JSON
also without `--json`, formatted for reading. Errors use
`mcp-manager/error-1` with the exported `McpError.to_dict()` fields.

## Install the CLI

As an alternative to downloading a release archive, use the hosted beta installer
on Linux/macOS:

```sh
curl -fsSL https://installer.aixand.org/manager/mcp | sh
```

It bundles Python and dependencies, asks for confirmation, and keeps software
separate from existing manager data. See
[hosted installation](../docs/release-installation.md#hosted-installer-alternative)
for supported targets, Windows PowerShell, script review, and custom stores.
Python API consumers install the package in their own application's environment.

## Home and independent API

`home` is the MCP store itself. Selection is explicit `home`, `MCP_MANAGER_HOME`,
`AIXAND_MANAGER_ROOT/mcp`, then
`${XDG_DATA_HOME:-~/.local/share}/aixand/manager/mcp`. Paths are canonical absolute
locations. Construction and read-only inspection do not create an absent store.
No user-data migration occurs. Agent Manager explicitly supplies its existing
sibling `mcp` path; the standalone override cannot redirect an Agent instance.

New stores contain no server definitions. MCP Manager does not
bundle, install or register Sonar or Lore. Users must install server software and
explicitly register its definition before attaching or preparing it. Existing
saved definitions remain available without being rewritten.

```python
from mcp_manager import AUTO, Manager

manager = Manager(home="/absolute/path/to/mcp")
manager.catalog()
created = manager.add("docs", {"command": "python3", "args": ["/path/to/server.py"]})
manager.add("sonar", {"kind": "sonar", "command": "/path/to/manually-installed/sonar"})
manager.show("docs")
manager.configure("docs", {"tool_timeout_sec": 90}, expected_revision=created["revision"])
manager.resolve(["docs", "sonar"], "/path/to/project", project=AUTO, strict=False, check=False)
manager.status("/path/to/project", servers=["sonar"], check=True)
job = manager.prepare(["sonar"], "/path/to/project", settings={"resolve": True})
manager.job(job["id"])
manager.jobs(project="/path/to/project")
manager.refresh(["sonar"], "/path/to/project")
```

`configure(..., expected_revision=None)` and `add()` create only absent names.
Replacement requires the observed revision. Sonar/Lore definitions are absent
until explicitly registered; names alone do not infer a project adapter.
Configuration is partial: omitted fields retain their current values.
Revisions cover normalized definitions with the retained spaced JSON encoder;
no revision field is inserted into stored records. Environment values remain
variable-name references. Existing Agent `configure_mcp` remains unguarded.

Resolution takes explicit ordered names, deduplicates first occurrence and keeps
the 64-name limit. There is no profile registry. `project=PATH` selects exactly
that canonical directory, `None` omits project presets, and `AUTO` finds the nearest
prepared ancestor or Git root (including a worktree `.git` file), then uses the
working directory. Same-named project directories and separate worktrees have
distinct path hashes. CLI resolution uses `--project PATH` / `--no-project`.

## Preparation and freshness

After manually installing a server, register its executable and adapter explicitly:

```sh
mcp-manager add sonar --definition '{"kind":"sonar","command":"/absolute/path/to/sonar"}'
mcp-manager add lore --definition '{"kind":"lore","command":"/absolute/path/to/lore"}'
```

Default status inspection includes only registered project adapters. A fresh store
reports no project indexes, and preparation rejects unregistered servers.

```sh
mcp-manager prepare sonar lore --project /path/to/project --background --key UUID --json
mcp-manager refresh sonar --project /path/to/project --resolve --resources yaml --json
mcp-manager status --project /path/to/project --servers '["sonar"]' --check --json
mcp-manager job UUID --json
mcp-manager jobs --project /path/to/project --json
```

Preparation/refresh CLI commands wait by default. `--background` returns the
retained job immediately; library methods always return immediately. Workers run
as `python -m mcp_manager.jobs`, survive client exit, and use the initiating
interpreter. `--settings JSON` accepts `resolve`, `model_dir`, `usage_config`,
`resources` (`none`/`yaml`) and `install_model`; corresponding CLI options are
also available and override supplied settings. Model installation requires an
explicit request.

UUID keys retain the existing signature over tools, canonical project, refresh
and normalized settings. Same request returns the captured job, even after a
definition changes; changed arguments conflict. Dead recorded workers report
`interrupted` on inspection without replay or rewriting their on-disk status.
Jobs preserve recorded states and partial completed-tool results. `jobs()` retains the
existing newest-50 enumeration limit. A queued record without a PID stays
queued; inspect its log and use a new key only for a new intent.

Each tool validates its SQLite root and stdio health before publishing the
manifest atomically. Failed refresh leaves the previous prepared generation
available. Old and failed staging generations are retained. Saved session
selections continue to refer to their captured generation. Preparation and
configuration do not modify a loaded session. `status(check=True)` performs
bounded read-only stdio health/freshness probes; an unavailable probe does not
turn a valid prepared generation into a missing generation. This status describes
preparation, not a live backend connection.

## Runtime operations and CLI results

See [runtime connectors](docs/connectors.md) for the integration contract and
[managed definitions](docs/MANAGED.md) for the owned resource lifecycle.

```sh
mcp-manager runtime capabilities --connector your_backend:factory --target @target.json --json
mcp-manager runtime status --connector your_backend:factory --target @target.json --scope session --json
mcp-manager runtime reconnect --connector your_backend:factory --target @target.json --scope session --operation-id request-1 --expected-selection-digest sha256:OBSERVED --json
mcp-manager runtime operation request-1 --json
```

`factory(options)` returns the connector; `--options JSON` is call-scoped.
Targets and connectors are explicit. Async `runtime_capabilities`, `runtime_status`
and `reconnect` accept the connector directly. `runtime_operation` inspects a
retained receipt locally. Reconnect preserves the selected configuration and
prepared generations. It does not refresh indexes, change profile assignments or
hot-attach a server. Native process reload support needs adapter evidence; the
library makes no unconditional Codex/ACP reconnect claim.

Exit codes are 0 for success/accepted/completed, 2 for validation/conflict/not-found,
3 for unsupported, 4 for uncertain, and 1 for operational failure or a
failed/interrupted preparation job. Interrupting a waiting client reports a
recovery reference and leaves its retained job/intent available.

## Verification and exclusions

```sh
python -m unittest discover -s tests -v
```

Fixtures use temporary stores, local stdio servers and injected connectors, without
provider credentials/model calls. Tests cover colliding projects/worktrees, stale
revisions, captured-job reuse, failed refresh, unavailable probes, detached jobs,
explicit scope, selection/generation preservation and uncertain reconnect delivery.

Remote HTTP/OAuth, automatic generation deletion,
marketplaces, remote sharing, cloud hosting and plugin-manager work remain deferred.
Profile assignments and launch snapshots belong to Agent Manager; native backend
calls belong to supplied adapters. The package imports none of Agent Manager,
Suite, Pathspec, Runner or Carrier.

`Manager.inspect(servers, cwd, project=...)` inspects independent MCP selections
without starting servers or preparing indexes. An unknown name or missing
executable is reported per item; other selections remain available. Integrity and
permission errors still propagate. This is preparation/configuration evidence,
separate from the live inventory APIs and connection status.

## Owned publisher definitions

Managed registrations preserve an accepted local configuration, publisher baseline,
incoming changes and immutable history. New ordered updates activate automatically
only for an uncustomized copy. Customized configurations keep working until reviewed.
See [managed definitions](docs/MANAGED.md) for registration, reconciliation, CLI,
Agent Manager attachments, migration and preparation.

## Owned publisher artifacts

Python integrations can call `Manager.capture_artifact(bytes_or_path)` to retain
an immutable, digest-named executable bundle under the MCP library's `artifacts`
directory. The returned descriptor contains `path`, `digest`, and `size`; capture
does not run the artifact or install dependencies. Repeated identical captures
reuse the owned file. Publishers can reference that path in registered definitions
so later source changes cannot silently replace an accepted executable.

## Internal architecture

`manager.py` is the public SDK facade and composes services instead of inheriting
source or managed-definition mixins. CLI commands call the same SDK operations.

| Package | Responsibility |
| --- | --- |
| `library/` | Definition validation, local registry, publisher reconciliation, history, review views, artifacts, and selection resolution. |
| `storage/` | Atomic files, locking, platform primitives, and compatible metadata encodings. |
| `sources/` | MCP discovery/import/update policy, provenance, and shared GitHub acquisition adapter. |
| `installations/` | Installation recipes, package execution, retained generations, and executable resolution. |
| `preparation/` | Stage and validate project indexes; Sonar/Lore commands live in separate adapters. |
| `jobs/` | Retain preparation/source/install requests, dispatch detached workers, and inspect receipts. |
| `diagnostics/` | Bounded stdio protocol checks. |
| `live/` | Connector contracts, inventory, and selection-preserving reconnect operations. |
| `interfaces/cli/` | Argument parsing, strict JSON input, SDK dispatch, and result presentation. |

Domain modules do not import CLI code, the SDK facade, or public re-export
modules. Public module paths include `mcp_manager.cli`,
`mcp_manager.runtime`, managed helpers, and `python -m mcp_manager.jobs`. Reconnect
receipt instrumentation through the runtime module shares the canonical
implementation.

Source and installation services are native to MCP Manager. Stored source hashes
and schema identifiers remain readable without external service packages. Native
services use explicit repository/engine dependencies; accessor methods delegate
to the same services. Client/project connections and the complete
uninstall/version lifecycle are subsequent work.

Builds use a clean temporary source copy so only selected modules enter wheels.
Store operations preserve revision encodings, home selection, and generated-server
naming conventions.

`tests/test_architecture.py` checks SDK signatures, import direction, public module
exports, and worker entry points. `tests/test_standalone.py` checks the external
import boundary and runs copied sources with site packages disabled, allowing
only the two declared utilities. Source adapter tests cover local creation,
imports, and retained background jobs without network access.

## Source imports and installations

Source discovery captures immutable bytes before import. Import and tracking do
not execute packages. Installation is an explicit retained operation and does
not accept a pending publisher update automatically.

```sh
mcp-manager managed discover https://github.com/owner/repository --json
mcp-manager managed import-discovery sha256:DISCOVERY --select server.json --json
mcp-manager managed install PUBLISHER SERVER --expected-revision sha256:STATE --json
mcp-manager managed source-job UUID --json
```

Supported recipes are exact npm/PyPI versions and captured Node/Python source.
Node source requires a lockfile; Python uses an isolated environment and retained
wheels. Host runtimes remain prerequisites. Failed installation leaves the prior
usable generation available. Historical configurations keep their installation
recipes and can resolve the original generation.

Stored accepted configurations, source/discovery objects, installation manifests,
and job signatures remain readable. Completed request keys return their recorded
results without replay. Stored-record fixtures verify reads without external
service imports or changes to the recorded bytes.

For an offline distribution check, provide a wheelhouse containing MCP Manager
and both utility wheels, then run:

```sh
python tests/verify_installation.py --wheelhouse /path/to/wheels
```

The verifier installs only those three distributions into a disposable environment,
enforces worker dependency boundaries, and exercises source updates,
stored records, captured-source installation, and the installed CLI. Node fixture
coverage is skipped when Node/npm are unavailable.

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

The default product directory is `${XDG_DATA_HOME:-~/.local/share}/aixand/manager/mcp`.
Set `AIXAND_MANAGER_ROOT=/custom/managers` to use `/custom/managers/mcp`
without changing the data location of unrelated applications. Unset, empty, or
relative `XDG_DATA_HOME` values fall back to `~/.local/share`.
Explicit CLI/SDK paths and product-specific environment overrides take precedence;
these paths are used directly. Existing configured storage paths remain supported.
Agent and Conversation config overrides remain separate from their state-home overrides.
Default path resolution creates no directories and does not move or adopt old stores.
To keep using an existing store, supply its original path explicitly. Native provider
homes connected by the user and operating-system temporary files retain their locations.
