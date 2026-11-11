# Owned creation and source imports

Each resource manager owns its content and update policy. Use the manager's public
SDK or CLI for creation, import, review, and installation:

| Manager | Responsibilities | Guide |
| --- | --- | --- |
| Prompt Manager | Local prompt creation/import, publisher registration, rendering, and reconciliation | [Managed prompts](../prompt-manager/docs/MANAGED.md) |
| Skill Manager | Skill creation/import, optional GitHub acquisition, source tracking, and retained jobs | [GitHub sources](../skill-manager/docs/github-sources.md) |
| MCP Manager | Server definitions, source discovery/import, retained jobs, and explicit runtime installation | [MCP Manager](../mcp-manager/README.md) |

Prompt Manager's package lifecycle SDK supplies owned-content reconciliation for
Skill Manager. The filesystem and GitHub utilities supply locking and acquisition;
manager services own native formats, source policy, and job execution.

## Local creation and import

Local creation stores content under publisher `local`. Local import copies content
into owned storage; it does not establish ongoing filesystem tracking. Explicit
publisher registration supports applications that supply bundled content.

```sh
prompt-manager managed create review --content @instructions.md
prompt-manager managed import-local /absolute/path/to/pack
skill-manager managed create review --description 'Review changes before merging' --content @SKILL-body.md
skill-manager managed import-local /absolute/path/to/skill
mcp-manager managed create docs --definition @server.json
mcp-manager managed import-local /absolute/path/to/server.json
```

See the individual manager guides for required inputs, identity rules, and guarded
mutations. Existing draft registrations and local definitions are adopted only
through an explicit operation; opening a store does not convert them.

## GitHub acquisition and jobs

Skill Manager's optional `github` extra and MCP Manager's source services acquire
public GitHub content. Prompt Manager accepts local or caller-supplied content;
remote acquisition and background import jobs belong to the integrating application.

```sh
skill-manager managed discover https://github.com/owner/repo --path skills --ref main
skill-manager managed import-github https://github.com/owner/repo/tree/main/skills/review
mcp-manager managed import-github https://github.com/owner/repo --ref v1.2.0
```

Source services resolve references to commits and capture selected content before
acceptance. Discovery and import do not execute repository hooks. Package validation,
size limits, and supported URLs are documented by each manager.

Retained job APIs expose progress and results. Request keys identify one intent;
changing arguments under the same key conflicts. Completed requests return recorded
results, and failed or interrupted work is not automatically replayed. Cancellation
checks process identity before signalling a worker.

## Updates and reconciliation

Source updates preserve customized accepted content until explicit review. Guarded
mutations reject stale revisions. Rollback retains a hold against automatic publisher
advancement. Reconciliation establishes the accepted content and publisher baseline.
See [Skill Manager managed content](../skill-manager/docs/managed.md),
[Prompt Manager reconciliation](../prompt-manager/docs/MANAGED.md), and
[MCP Manager managed definitions](../mcp-manager/docs/MANAGED.md) for native policies.

## MCP installation and runtime identity

MCP runtime installation is explicit. Supported recipes include exact npm/PyPI
versions and captured Node/Python sources. Installation may execute package build
scripts; discovery and registration do not install software.

Prepared generations retain their manifests and dependencies. Failed preparation
preserves the usable generation. Historical configurations resolve their captured
generation, and existing sessions retain their physical launch configuration.
Host runtimes and the operating system remain execution prerequisites.

See [MCP installation](../mcp-manager/README.md) for recipe requirements,
readiness checks, retained logs, and offline distribution verification.

## Application integration

Applications call each manager's public APIs and own their resource selection,
scheduling, and delivery. Agent Manager's resource integrations are optional;
see [resource separation](../agent-manager/docs/resource-separation.md) for launch
behavior. Resource availability and installation readiness are evaluated on the
execution machine. Content import does not grant provider delivery support.
