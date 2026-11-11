# MCP Manager: Role, Current Features, and Future Direction

Status: Product discussion and proposed roadmap

This document records the intended role of MCP Manager, the features present in
the current source checkout, and proposed improvements. The roadmap is a
discussion baseline; proposed features are not implemented guarantees or an
approved delivery schedule.

## Main purpose

**Install, configure, and maintain MCP servers in one shared library, then make
them available to agents registered in Agent Manager.**

A user should be able to configure an MCP once, select which agent profiles use
it, and maintain its installation and settings from one place. The target
experience covers every agent supported by Agent Manager, with capabilities
reported for each provider and launch mode.

For example, a user could add a documentation server, install its supported
package, configure its required environment variable references, test it, and
assign it to several agent profiles. The profiles reuse the definition and
installation; their providers manage the running MCP processes.

MCP Manager also remains usable independently through its Python API and CLI.
Its library accepts explicit MCP selections and project context without needing
Agent Manager's profile registry.

## Responsibilities across the suite

| Component | Responsibility |
| --- | --- |
| MCP Manager | Own reusable MCP definitions, source imports, supported installations, configuration history, project preparation, and connector-based status/reconnect coordination. |
| Agent Manager | Own agent profiles, MCP assignments, selection policies, launch overrides, and captured session configurations. Deliver selected MCP configurations through provider integrations. |
| Backend adapters | Implement provider-specific live operations and advertise supported inventory, reconnect, and delivery capabilities. |
| Integrating applications | Present the management UI and combine information from the managers into a coherent user experience. |
| Shared `resource-sources` package | Supply source discovery/import, retained source jobs, and MCP installation mechanisms used by MCP Manager. |
| Individual MCP servers | Implement their tools and any server-specific indexing or processing. |

A unified management screen can expose all these operations while changes to
profile assignments still go through Agent Manager. Maintaining one authority
for each kind of state keeps CLI, SDK, and UI behavior consistent.

## Features currently implemented

This inventory follows the current source, including features inherited from
`resource-sources`. Supported installation is available through explicit managed
installation recipes. Sonar/Lore project preparation itself does not install their
server executables.

### Catalog and configuration

| Feature | Current behavior |
| --- | --- |
| Shared catalog | Register, list, show, and resolve named MCP definitions in a shared store. |
| Server configuration | Store executable paths/names, arguments, working directories, environment variable references, startup timeouts, and tool timeouts. |
| Validation | Validate definition fields, names, paths, arguments, timeout values, and supported installation recipes. |
| Guarded edits | Use observed revisions to reject conflicting edits. Standalone local configuration supports partial updates; managed saves replace a complete definition. |
| Owned definitions | Create local managed definitions, register publisher definitions, and adopt existing local definitions. |
| Independent resolution | Resolve an explicit ordered selection against a working directory and optional project context. |
| Read-only inspection | Inspect availability and configuration without starting servers or preparing indexes; report unavailable selections individually where supported. |
| Store selection | Use an explicit home, environment override, or default local store. Agent Manager supplies its shared sibling MCP store explicitly. |

Environment values are supplied at execution. Definitions and configuration
history retain variable-name references rather than credential values.

### Discovery, imports, and installation

| Feature | Current behavior |
| --- | --- |
| Local imports | Copy local server definitions into the owned library. Repeated imports preserve customized copies; they do not establish continuous filesystem tracking. |
| Public GitHub imports | Discover candidates from repository, tree, or file URLs; select and import a captured source snapshot. |
| Source provenance | Retain repository identity, URL, reference, resolved commit, candidate path, and source digest. |
| Package detection | Recognize supported `server.json` package entries, Node executables, and Python console scripts. Detection is a suggestion of a launch method, not proof of MCP compatibility. |
| npm/PyPI installation | Explicitly install supported packages at exact versions. |
| Source installation | Build captured Node or Python source. Node source requires a lockfile; Python installations use an isolated environment and captured wheels. |
| Installation generations | Retain distinct runtime generations and manifests. Historical managed configurations can resolve their original installed generation. |
| Candidate preparation | Install an active, incoming publisher, or reviewed reconciliation candidate before activation. Installation and accepting a configuration update are separate operations. |
| Setup diagnostics | Report unsupported installation methods, missing launch metadata, unavailable executables, and installation requirements. |
| Artifact capture | Retain digest-identified immutable executable bundles supplied by integrations. Capturing an artifact does not execute or install it. |

Installation is explicit. Registration, discovery, and agent launch do not
silently install packages. Host Node/Python runtimes remain prerequisites, and
externally configured commands remain externally managed. Fresh stores contain
no bundled server definitions.

### Updates, customization, and history

| Feature | Current behavior |
| --- | --- |
| Publisher updates | Register incoming definitions and track publisher baselines separately from the accepted configuration. |
| Source update checks | Check GitHub sources, inspect diagnostics, and switch tracked references. |
| Tracking controls | Pin sources, pause tracking, configure check intervals, and request due checks. Standalone automatic scheduling belongs to the caller. |
| Automatic acceptance | Accept eligible forward updates for unmodified copies when usable. Other changes can require review. |
| Customization preservation | Keep customized accepted configurations available while incoming changes wait for review. |
| Reconciliation | Preview structural differences, resolve conflicts, and apply a guarded update. |
| History and rollback | Inspect immutable configuration history, resolve historical revisions, and restore earlier configurations. Source rollback establishes a hold against automatic reactivation. |
| Failure preservation | Retain accepted content and expose diagnostics when fetching, validation, installation, or update preparation fails. |

### Project preparation and jobs

| Feature | Current behavior |
| --- | --- |
| Sonar/Lore preparation | Coordinate registered server commands to build or refresh project indexes. Indexing implementations belong to those servers. |
| Preparation options | Support applicable resolution, YAML resources, model directories, usage configuration, and explicitly requested model installation. |
| Project isolation | Distinguish canonical project paths, same-named directories, and separate worktrees. Managed preparation also separates publisher/server namespaces. |
| Validated publication | Validate project roots and stdio health before publishing a prepared index generation. |
| Refresh preservation | Keep the previous usable generation when refresh fails. Existing session selections retain their captured generation. |
| Freshness probes | Perform bounded read-only checks for supported project servers and report stale or unavailable observations. |
| Retained jobs | Run preparation and source/import/install operations in retained workers that can survive client exit. |
| Job inspection | Inspect status, results, and retained local logs. Reused request keys return the recorded intent and reject changed arguments. |
| Cancellation | Support cancellation for source jobs. Preparation-job inspection reports interrupted workers without automatic replay. |

### Runtime operations and interfaces

| Feature | Current behavior |
| --- | --- |
| Connector discovery | Ask an injected connector which inventory and reconnect operations it supports. |
| Live inventory | Normalize observed connection/authentication status and tool inventory where the connector supplies them. |
| Scoped reconnect | Request supported server, session, or process operations with explicit selection-preservation checks. |
| Operation receipts | Retain reconnect intent/results, detect conflicting operation reuse, and report uncertain delivery without automatically replaying it. |
| Python API and CLI | Expose independent library calls and JSON-capable commands with structured operational errors. |

Configuration readiness, installation readiness, project-index freshness, and
live connection status are separate observations. A successful installation does
not establish that an agent has connected to the server. Reconnect preserves the
captured selection; it does not refresh indexes or change profile assignments.

## Existing Agent Manager integration

The following features form part of the complete user experience, with their
assignment and session state owned by Agent Manager:

- Attach or detach MCPs and save profile defaults.
- Inherit defaults, add or exclude MCPs for a launch, or launch without managed
  MCPs.
- Mark selections required or optional. Unavailable required selections block
  delivery; optional omissions retain a report.
- Capture selected definitions and prepared/installed generations for sessions
  and subsequent resume behavior.
- Compose application resource requirements with profile and launch selections.
- Deliver configurations through provider-specific integrations. Terminal
  delivery paths exist for Codex, OpenCode, Claude, Copilot, and Antigravity.

SDK delivery, live inventory, reconnect, and changes to loaded sessions depend on
the provider adapter and launch mode. Terminal delivery support alone does not
establish support for those live operations.

## Current gaps

The core library is already broader than a configuration registry. The main
product gap is completing and exposing the everyday lifecycle across agents.
Specific gaps include:

- A consolidated view of MCP usage and observed status across all agent profiles.
- A complete repair/reinstall/uninstall workflow and reference-aware storage cleanup.
- Guided configuration and comprehensive diagnostics for arbitrary registered servers.
- Remote HTTP transports and OAuth lifecycle management.
- Authenticated private GitHub source discovery/import.
- Search across registry-compatible server catalogs.
- Portable manifests covering definitions, versions, and agent assignments.
- Uniformly exposed compatibility information and supported live changes.

Old generations are retained; automatic runtime/index garbage collection is not
enabled. Existing sessions may still reference them. Current source tracking and
installation should be extended from their existing implementations.

## Proposed roadmap

### First: complete the everyday workflow

| Proposal | Expected experience |
| --- | --- |
| Agent-wide management view | Open an MCP and see assigned profiles, accepted version, installation readiness, and observed connections. Bulk assignment delegates to Agent Manager. |
| Complete installation lifecycle | Install, update, reinstall, repair, and uninstall with an impact preview for affected profiles and retained sessions. |
| Guided configuration | Present required settings, credential references, defaults, validation, and the effective configuration for a selected profile/project. |
| Comprehensive `doctor` | Check runtime/executable availability, required values, protocol compatibility, exposed capabilities, and delivery to a selected agent. Explain how to fix failures. |
| Compatibility matrix | Show configuration delivery, live inventory, reconnect, live changes, and restart requirements separately for each provider and launch mode. |

The first milestone should support this complete path:

**Add MCP → install → configure → test → assign to agents → inspect status →
update or remove.**

### Next: expand connectivity and portability

| Proposal | Expected experience |
| --- | --- |
| Remote MCP connections | Configure Streamable HTTP endpoints and supported request settings. Report support per backend. |
| Authentication lifecycle | Use credential-store references and provide OAuth login, refresh, expiry diagnostics, and logout for supported remote servers. |
| Import existing agent configurations | Discover existing provider configurations, preview duplicates/conflicts, and import selected entries into the shared library. |
| Searchable discovery | Search configured registry-compatible catalogs, inspect package/configuration metadata, and import a chosen version. |
| Portable project manifests | Export/import definitions and assignment intent; record accepted versions in a manifest or lockfile while resolving credentials and machine-specific paths locally. |
| Explicit update activation | Preview configuration/tool changes, prepare and validate a candidate, then select which future launches use it. Preserve existing session selections until an explicit supported migration. |

Remote transport and authentication work should follow the MCP
[transport specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)
and [authorization specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization).
Catalog discovery should use the standardized metadata and registry interfaces
described in the [MCP Registry documentation](https://modelcontextprotocol.io/registry/about).

### Later: maintenance and additional sources

| Proposal | Expected experience |
| --- | --- |
| Reference-aware storage management | Show disk usage and identify installation/index generations eligible for deletion after checking retained references. |
| Additional installation formats | Add container-based installations and other package formats where usage justifies them. |
| Private sources | Support authenticated repository and package-registry access through credential references. |

## Design principles

1. Keep the common workflow simple while retaining advanced configuration through
   the CLI and Python API.
2. Keep MCP definitions/installations authoritative in MCP Manager and profile
   assignments/session selections authoritative in Agent Manager.
3. Reuse definitions and installations across agents while letting providers own
   their MCP process lifetimes.
4. Treat configuration, installation, project preparation, and live connection
   status as distinct states in APIs and UI.
5. Preserve customized configurations and working generations during updates.
6. Make activation and effects on active sessions explicit.
7. Advertise capabilities per provider and launch mode, with clear unsupported
   and unknown states.
8. Resolve credentials on the execution machine and retain references in shared
   configuration/history.

## Implementation references

- [Core manager API](../src/mcp_manager/manager.py)
- [Standalone CLI](../src/mcp_manager/cli.py)
- [Managed CLI and source command delegation](../src/mcp_manager/managed_cli.py)
- [Managed definitions and reconciliation](MANAGED.md)
- [Runtime connector contract](connectors.md)
- [Owned artifact capture](../src/mcp_manager/artifacts.py)
- [Shared source/import/installation documentation](../../docs/resource-imports.md)
- [Owned source API](../src/mcp_manager/sources/service.py)
- [Installation implementation](../src/mcp_manager/installations/service.py)
