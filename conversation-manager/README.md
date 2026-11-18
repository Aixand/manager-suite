# Conversation Manager

Retained backend conversations for chat interfaces: text turns, events, approvals,
queues, settings, cancellation, and reconnecting to running work.

This development branch supports Python 3.11–3.14. Provider capabilities and
native platform qualification vary. Agent Manager owns provider environments and
sign-in; Conversation Manager owns conversation protocols and retained controls.

From this package directory, with the package installed in your Python environment:

```sh
conversation-manager demo --fixture --json
```

This explicitly injected offline SDK fixture runs a real local worker, detaches,
reattaches, replays events, and terminates its worker. It needs no provider account.
A completed fixture run does not qualify authenticated provider behavior.

[Documentation and reader tasks](docs/README.md) · [Architecture](docs/architecture.md)

## Install the CLI

See [install the cli](docs/getting-started.md).

## Python interface

See [python interface](docs/frontend-api.md).

## Agent Manager boundary

See [agent manager boundary](docs/integrations.md).

## Editable configuration and default layout

See [editable configuration and default layout](docs/configuration.md).

## Worker process ownership

See [worker process ownership](docs/runtime.md).

## Slash-command discovery SDK

See [slash-command discovery sdk](docs/slash-commands.md).

## OpenCode frontend path

See [opencode frontend path](docs/integrations.md).

## Scope and status

See [scope and status](docs/architecture.md).

## Codex and OpenCode controls

See [codex and opencode controls](docs/settings.md).

## Message queues

See [message queues](docs/message-queues.md).

## Local developer commands

See [local developer commands](docs/development.md).

### Default storage location

See [path defaults and precedence](docs/configuration.md#default-storage-location).

Shared [participation](../CONTRIBUTING.md), [support](../SUPPORT.md),
[security](../SECURITY.md), and [license](../LICENSE) policies apply.
