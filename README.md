# Manager Suite

Five managers for local agent environments, retained
conversations, and reusable resources. Maintained by Artificial Intelligence
Advancement Initiative Inc. Each product can be installed independently; the
collection installs all five.

## Try the beta candidate

Start with the [candidate notes](docs/beta-release-notes.md),
[platform and provider limits](docs/beta-support.md), and
[hosted and archive installation guide](docs/release-installation.md).

Native releases target Linux x64/ARM64 with glibc, macOS ARM64, and Windows x64.
Prompt, Skill, and MCP Manager also target Intel Macs. Agent, Conversation, and
the collection defer Intel Mac installers for this beta. Attached qualification
reports describe each archive's tested platform and minimum operating system.
Provider executables, authentication, and runtime services are separate
integrations. POSIX bootstraps use `sh`, `curl`, `tar`, and `sha256sum` or
`shasum`; Windows bootstraps use PowerShell 5.1+ and the system `tar.exe`.

| Component | Distribution | Commands | Responsibility |
| --- | --- | --- | --- |
| [Agent Manager](agent-manager/README.md) | `aixand-agent-manager` | `agent`, `agent-manager-mcp` | Native environments, authentication profiles, and launch context |
| [Conversation Manager](conversation-manager/README.md) | `aixand-conversation-manager` | `conversation-manager` | Retained conversations, messages, approvals, settings, and queues |
| [MCP Manager](mcp-manager/README.md) | `aixand-mcp-manager` | `mcp-manager` | MCP definitions, preparation jobs, and connector operations |
| [Prompt Manager](prompt-manager/README.md) | `aixand-prompt-manager` | `prompt-manager` | Owned prompts, composition, publisher reconciliation, and snapshots |
| [Skill Manager](skill-manager/README.md) | `aixand-skill-manager` | `skill-manager` | Owned skills, publisher reconciliation, validation, and export |

All five managers default to `${XDG_DATA_HOME:-~/.local/share}/aixand/manager/<product>`.
`AIXAND_MANAGER_ROOT` relocates the default config and data directories together;
explicit product and instance paths take precedence. Software installation does
not move or adopt existing data stores automatically.

See [resource imports](docs/resource-imports.md) for owned creation, public GitHub
imports, and explicit MCP preparation. Python API dependencies stay inside their
consuming product's environment. Applications can integrate the managers through
their public APIs without a separate host application.

## Install a manager

As an alternative to downloading release archives, install one manager on
Linux/macOS with the hosted beta installer:

```sh
curl -fsSL https://installer.aixand.org/manager/agent | sh
```

Replace `agent` with `conversation`, `mcp`, `prompt`, or `skill` for another
manager. The installer bundles Python and dependencies, asks for confirmation,
and preserves existing manager data. Provider installation, authentication, and
runtime services remain separate. The all-five `/manager` collection route is
reserved and unavailable until a qualified collection release is registered.
See [installation options](docs/release-installation.md#hosted-installer-alternative)
for Windows PowerShell, script review, exact versions, and custom software stores.

## Develop a package

Each manager owns development commands, a lock, and a local `.venv`. POSIX example:

```sh
cd agent-manager
./dev.sh develop
./dev.sh check
./dev.sh test
./dev.sh build
```

PowerShell entry points use `dev.ps1`. Use `--local` explicitly for sibling source
dependencies; normal setup consumes versioned packages. See
[package development](docs/DEVELOPMENT.md) for prerequisites and source layouts.

Collection development requires uv 0.12.7 and Python 3.11–3.14. The default is
Python 3.11; set `UV_PYTHON=3.14` before `develop` to choose that tested minor.

```sh
./dev.sh doctor
./dev.sh develop
./dev.sh check
./dev.sh test
```

Development installs the five managers with
published libraries, Carrier, and the Undercurrent SDK. The checked-in locks and
[dependency pins](.github/ci/dependencies.json) select exact versions and hashes.

## Build installers from source

These commands require a source checkout and the build inputs documented in
[product installation](docs/INSTALLATION.md). Downloaded archive installation is
described separately in [Install a release](docs/release-installation.md).

```sh
python3 -B scripts/build_installers.py --output dist/installers
./dev.sh install --artifact dist/installers/aggregate --prefix "$HOME/.local/share/aixand/products"
export PATH="$HOME/.local/share/aixand/products/bin:$PATH"
agent --help
```

The build produces a collection and independent product artifacts with a bundled
Python runtime. Shared installs and application-owned private prefixes have
separate ownership. See [selective CI and independent releases](docs/releases.md)
for candidate builds and product versioning. Prompt/skill content releases are
local application features, separate from software publishing.

## AI-assisted development

We use AI tools to generate and revise code and documentation during development
of Manager Suite and review the resulting work. Maintainers remain responsible
for validation and preserving third-party attribution. We do not knowingly
include proprietary code without permission.

If you identify material that we lack permission to distribute, follow the
[private reporting instructions](SUPPORT.md#report-code-ownership-or-licensing-concerns).
We investigate credible ownership reports and remove or replace material when
the evidence establishes that we lack distribution rights. AI assistance does
not excuse unauthorized use of someone else's work.

## Issues and pull requests

Bug reports, documentation issues, feature requests, and usage questions are
welcome. We are not accepting pull requests for now because our team lacks the
capacity to review external contributions. See the [participation policy](CONTRIBUTING.md)
and [support guide](SUPPORT.md).

Browse the [documentation index](docs/README.md) for guides and references.
For local development, use [package development](docs/DEVELOPMENT.md).
Read the [Code of Conduct](CODE_OF_CONDUCT.md) and [security policy](SECURITY.md).
Changes are listed in the [changelog](CHANGELOG.md); the
[application integration guide](docs/integration-architecture.md) describes component boundaries.

Original project material uses [Apache-2.0](LICENSE). See the
[dependency and notice inventory](docs/DEPENDENCIES.md) for distributed material.

## Application integration

Use [Conversation Manager](conversation-manager/README.md) for retained conversations
and its [frontend API](conversation-manager/docs/frontend-api.md) for application
startup, attachment, and recovery. See [provider SDK integration](docs/sdk-backends.md)
for the owning references.

See [the integration architecture](docs/integration-architecture.md) for dependency
boundaries, optional installations, application preparation and workflow resource
capture. Use [Agent Manager’s public SDK](agent-manager/docs/python-sdk.md) for
native environments and execution; applications compose resource libraries separately.

