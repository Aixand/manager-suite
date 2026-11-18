# Resolve selections and guard local edits

Resolve explicit names to client configuration, or inspect availability without
starting a server. Local definitions and publisher-owned definitions have separate
edit contracts; use [managed definitions](MANAGED.md) for publisher content.

## Home and independent API

`home` is the MCP store itself. Selection is explicit `home`, `MCP_MANAGER_HOME`,
`AIXAND_MANAGER_ROOT/mcp`, then
`${XDG_DATA_HOME:-~/.local/share}/aixand/manager/mcp`. Paths are canonical absolute
locations. Construction and read-only inspection do not create an absent store.
No user-data migration occurs. The consuming application selects its MCP store
explicitly or uses the standalone precedence above. Agent Manager does not choose
a sibling MCP store or delegate MCP configuration.

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
variable-name references. The former Agent `configure_mcp` API is retired; use
this product's guarded API directly.

Resolution takes explicit ordered names, deduplicates first occurrence and keeps
the 64-name limit. There is no profile registry. `project=PATH` selects exactly
that canonical directory, `None` omits project presets, and `AUTO` finds the nearest
prepared ancestor or Git root (including a worktree `.git` file), then uses the
working directory. Same-named project directories and separate worktrees have
distinct path hashes. CLI resolution uses `--project PATH` / `--no-project`.


## Inspect and diagnose a selection

`inspect(servers, cwd, project=...)` preserves available items when another name is
unknown. It never prepares indexes or starts servers. Integrity and permission
errors still propagate. `resolve(..., check=False)` is also offline; `check=True`
can start bounded stdio probes for prepared Sonar/Lore adapters.

| State | Reader action |
| --- | --- |
| `configured` | Deliver the resolved stdio configuration to a client. |
| `prepared` | Deliver configuration with the captured project generation. |
| `missing-executable` | Correct the command or explicitly install its recipe. |
| `needs-project` | Supply project context for the project adapter. |
| `needs-preparation` | Prepare or refresh the selected project. |

`strict=True` raises for unavailable items except `needs-project`. No state here
establishes that a client connected. Native names use `agent_manager_` plus the
registered name; this compatibility prefix does not require Agent Manager.

### Default storage location


The default product directory is `${XDG_DATA_HOME:-~/.local/share}/aixand/manager/mcp`.
Set `AIXAND_MANAGER_ROOT=/custom/managers` to use `/custom/managers/mcp`
without changing the data location of unrelated applications. Unset, empty, or
relative `XDG_DATA_HOME` values fall back to `~/.local/share`.
Explicit CLI/SDK paths and product-specific environment overrides take precedence;
these paths are used directly. Existing configured storage paths remain supported.
Default path resolution creates no directories and does not move or adopt old stores.
To keep using an existing store, supply its original path explicitly. Native provider
homes connected by the user and operating-system temporary files retain their locations.

## Owning implementation

See [definition validation](../src/mcp_manager/library/definitions.py),
[resolution](../src/mcp_manager/library/resolution.py),
[registry](../src/mcp_manager/library/registry.py), and
[store tests](../tests/test_store.py). Revisions use sorted, spaced JSON with
Unicode preserved; switching to compact JSON changes existing revision tokens.

## CLI input and errors

`python -m mcp_manager` exposes the same CLI. Put `--home` before the command
and `--json` after the leaf command. JSON input accepts inline content or `@PATH`;
duplicate keys, nonfinite numbers, and invalid Unicode are rejected. Output is
JSON even without `--json`, formatted for reading. Errors use
`mcp-manager/error-1` and the exported `McpError.to_dict()` fields. See
[CLI input](../src/mcp_manager/interfaces/cli/input.py) and
[error definitions](../src/mcp_manager/errors.py).
