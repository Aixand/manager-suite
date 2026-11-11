# Agent Manager

The Python distribution is `aixand-agent-manager`; import `agent_manager` and run
`agent` or `agent-manager-mcp`.

Manage native agent profiles, environments and execution independently of other
managers. Supports Codex, Claude Code, Copilot, OpenCode and Antigravity, with
provider-specific capabilities and native login ownership.

## Native CLI

```sh
agent onboarding
agent codex onboarding
agent codex profile new main
agent codex profile default main
agent codex login main
agent codex run main
agent codex start
agent codex sessions
agent codex continue SESSION_REFERENCE --profile main
```

Other capabilities include profile aliases, connecting existing homes, environment
inspection/removal, provider installation/update/uninstallation, model discovery,
account allowance, diagnostics, saved launch arguments, and native instruction files:

```sh
agent codex config args add -- --yolo
agent codex config instructions edit --profile main
agent codex doctor --profile main
agent codex budget --all --refresh
agent codex models --profile main
```

Use `--help` after each command for supported options. Direct native execution is
the default. Optional `runtime = "undercurrent"` uses Undercurrent for managed CLI
execution and terminal attachment. Install `aixand-agent-manager[undercurrent]` and provide
the Undercurrent binaries and daemon for that mode.

## Python SDK

Use synchronous `Manager` for profile administration.
Use async `Client` with `aixand-agent-manager[sdk]` for managed native execution through
Undercurrent. SDK execution always requires Undercurrent; the CLI runtime setting
does not select an SDK runtime. Both `[sdk]` and `[undercurrent]` depend only on
`aixand-undercurrent-sdk`. The execution SDK provides guarded automatic startup,
folder-scoped session discovery/continuation, recoverable execution references, and
handles for state, terminal input/output, streams, waiting and stopping.

```python
from agent_manager import Manager

manager = Manager('codex')
print(manager.readiness('main'))
```

See the [Python SDK](docs/python-sdk.md), [configuration](docs/configuration.md),
[architecture](docs/architecture.md), [profile identities](docs/profiles-and-environments.md),
and [provider versions](docs/provider-versions.md).

## Boundaries

Conversation messaging, streaming, and approvals belong to
[Conversation Manager](../conversation-manager/README.md). Native provider
budget/model probes and the Codex CLI continuation adapter remain internal capabilities.

Prompt, skill, and MCP resource management and Carrier orchestration are external.
Agent Manager does not read historical resource assignments or expose resource
migration commands. Native configuration and existing user files remain untouched.
`agent_targets()` and `resolve_agent_target(environment_id)` support independent
tools through stable environment identities.

The optional `[mcp]` extra provides an administration interface through
`agent-manager-mcp`, not native MCP resource management. Base dependencies include
`aixand-lib-filesystem`, `aixand-lib-local-runtime`, JSON Schema and WebSocket support; no manager service is required.

## Development

Run `python -m unittest discover -s tests -t .` for automated tests. Opt-in
Undercurrent tests use isolated daemons and fixture providers. Build and update the
CLI with the [product installer](../docs/INSTALLATION.md).

Qualify the packaged SDK separately with
`python scripts/qualify_sdk.py --uc-bin /path/to/undercurrent/bin`. This uses an
isolated daemon and offline fixtures. See the [SDK v1 contract](docs/sdk-contract-v1.md)
for local dependency flags, artifacts, and the maintained consumer example.

Optional [live Codex qualification](docs/live-sdk-qualification.md) uses an existing
profile and daemon; it requires explicit `--allow-live` before making model calls.

Shared utilities are packaged from `../../aixand-libraries/filesystem` and
`../../aixand-libraries/local-runtime` in the sibling library checkout. Installed wheels declare these
package dependencies; release installers bundle them. No `aixand-platform`
package or external platform checkout is required.

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

The default product directory is `${XDG_DATA_HOME:-~/.local/share}/aixand/manager/agent`.
Set `AIXAND_MANAGER_ROOT=/custom/managers` to use `/custom/managers/agent`
without changing the data location of unrelated applications. Unset, empty, or
relative `XDG_DATA_HOME` values fall back to `~/.local/share`.
Explicit CLI/SDK paths and product-specific environment overrides take precedence;
these paths are used directly. Existing configured storage paths remain supported.
Agent and Conversation config overrides remain separate from their state-home overrides.
Default path resolution creates no directories and does not move or adopt old stores.
To keep using an existing store, supply its original path explicitly. Native provider
homes connected by the user and operating-system temporary files retain their locations.
