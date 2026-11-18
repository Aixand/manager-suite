# Agent Manager

Manage native agent profiles, environments, authentication selection, software policy, and terminal execution independently of other managers. The distribution is `aixand-agent-manager`; import `agent_manager`, or run `agent` and `agent-manager-mcp`.

Supports Codex, Claude Code, Copilot, OpenCode, and Antigravity according to provider capabilities. Providers own credentials and native login. Conversation Manager owns programmatic chat, streaming, and approvals.

## Start here

Follow [first use](docs/getting-started.md) for installation, native login, a terminal run, and continuation. Use the [documentation index](docs/README.md) to select a task.

```sh
agent --help
agent codex list
```

Direct terminal execution is the CLI default. Async SDK execution always requires Undercurrent; closing a client detaches without stopping work.

## Integrate and develop

- [Python SDK](docs/python-sdk.md) and [SDK v1 contract](docs/sdk-contract-v1.md)
- [Architecture and ownership](docs/architecture.md)
- [Configuration](docs/configuration.md) and [storage paths](docs/storage.md)
- [Provider versions and continuation qualification](docs/provider-versions.md)
- [Development](docs/development.md) and [offline SDK qualification](docs/sdk-contract-v1.md)
- [Persistent execution example](../docs/examples/persistent-agents.md)

Base dependencies include filesystem, local-runtime, JSON Schema, and WebSocket support. No other manager service is required. Prompt, skill, MCP resource delivery, and Carrier orchestration remain independently owned.

## Install the CLI

See [Install the CLI](docs/getting-started.md).

## Native CLI

See [Native CLI](docs/getting-started.md).

## Python SDK

See [Python SDK](docs/sdk-execution.md).

## Boundaries

See [Boundaries](docs/resource-separation.md).

## Development

See [Development](docs/development.md).

## Local developer commands

See [Local developer commands](docs/development.md).

## Default storage location

See [Default storage location](docs/storage.md).
