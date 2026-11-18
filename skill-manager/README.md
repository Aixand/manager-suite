# Skill Manager

Skill Manager captures complete native `SKILL.md` packages into owned local storage.
It validates content, preserves user edits across publisher updates, and exports
immutable selections or connects them to an explicit project directory.
It needs no provider credentials or model service.

Python 3.11+; distribution `aixand-skill-manager`, import `skill_manager`, command
`skill-manager` or `python -m skill_manager`. These guides describe the development branch.

## First use

In an environment with this package and its dependencies installed, run from this directory:

```sh
python -B examples/sdk_application.py
```

The example registers a publisher package, keeps a user edit during an update,
reconciles the update, and verifies exported file bytes. It uses temporary storage
and cleans up automatically. See [getting started](docs/getting-started.md) for setup.

## Find the owning guide

- [Documentation index](docs/README.md)
- [Architecture and ownership](docs/architecture.md)
- [Owned registration and reconciliation](docs/owned-content.md)
- [Project preview, apply, detach, and recovery](docs/project-delivery.md)
- [Python SDK and immutable selections](docs/sdk-entry-points.md)
- [Native authoring, CLI, validation, release, and export](docs/native-format.md)
- [Optional GitHub sources](docs/github-sources.md)
- [Storage contract](docs/storage.md)
- [Develop and verify](docs/development.md)

Suite installation and participation policies remain at the repository root.

## Install the CLI

See [Install the CLI](docs/native-authoring.md#install-the-cli).

## Install and verify

See [Install and verify](docs/native-authoring.md#install-and-verify).

## Public library

See [Public library](docs/sdk-entry-points.md).

## CLI

See [CLI](docs/native-authoring.md#cli).

## Format, release, and export contract

See [Format, release, and export contract](docs/native-format.md).

## Local developer commands

See [Local developer commands](docs/development.md).

## Default storage location

See [Default storage location](docs/native-authoring.md#default-storage-location).
