# MCP Manager

MCP Manager owns reusable stdio MCP definitions, publisher reconciliation, source
imports, installation generations, and project preparation. Applications supply
live connectors and own their running clients.

This development branch provides an independent Python 3.11+ API and CLI. Its base
dependencies are the filesystem and GitHub-source utilities; no other manager or
provider service is required.

From the suite root, install into your application's environment:

```sh
python -m pip install ./mcp-manager
mcp-manager --help
```

Start with [resolve without launch](docs/getting-started.md), then use the
[documentation index](docs/README.md). Contributors use the
[local development guide](docs/development.md).

[Architecture](docs/architecture.md) · [Owned definitions](docs/MANAGED.md) ·
[Live connectors](docs/connectors.md) · [Support](../SUPPORT.md) ·
[Security](../SECURITY.md) · [License](LICENSE)

## Install the CLI

See [installation](docs/getting-started.md#install-the-cli).

## Home and independent API

See [home and independent API](docs/resolution.md#home-and-independent-api).

## Preparation and freshness

See [preparation and freshness](docs/preparation.md#preparation-and-freshness).

## Runtime operations and CLI results

See [runtime operations and cli results](docs/operations.md#runtime-operations-and-cli-results).

## Verification and exclusions

See [verification and exclusions](docs/development.md#verification-and-exclusions).

## Owned publisher definitions

See [owned publisher definitions](docs/MANAGED.md).

## Owned publisher artifacts

See [owned publisher artifacts](docs/sources.md#owned-publisher-artifacts).

## Internal architecture

See [internal architecture](docs/architecture.md#internal-architecture).

## Source imports and installations

See [source imports and installations](docs/sources.md#source-imports-and-installations).

## Local developer commands

See [local developer commands](docs/development.md).

### Default storage location

See [home selection and path precedence](docs/resolution.md#default-storage-location).
