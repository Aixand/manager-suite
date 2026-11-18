# Manager Suite documentation

Use General for shared setup and integration. Choose a product to understand its
architecture, lifecycles and public contracts. These docs describe the current
source checkout; installation and qualification guides identify narrower release
or platform scope. Documentation standard: 0.1.0.

## General

| Reader goal | Start here |
| --- | --- |
| Install independent commands or the SDK environment | [Installation](getting-started/installation.md) |
| Verify local operation without a model | [Quick start](getting-started/quickstart.md) |
| Choose an owner for each concern | [Concepts](concepts.md) · [Integration architecture](integration-architecture.md) |
| Prepare content for an application | [Resource capture](application-resources.md) · [Imports](resource-imports.md) |
| Find exact interfaces | [Python map](reference/python.md) · [CLI map](reference/cli.md) |
| Recover a conflict or uncertain operation | [Troubleshooting](troubleshooting.md) |
| Change a manager | [Package development](DEVELOPMENT.md) |

## Product documentation

| Product | Owns | Documentation |
| --- | --- | --- |
| Agent Manager | Native environments, authentication profiles and execution | [Agent Manager](../agent-manager/docs/README.md) |
| Conversation Manager | Retained turns, approvals, queues and attachment | [Conversation Manager](../conversation-manager/docs/README.md) |
| MCP Manager | Definitions, preparation, resolution and connectors | [MCP Manager](../mcp-manager/docs/README.md) |
| Prompt Manager | Owned prompts, composition, snapshots and reconciliation | [Prompt Manager](../prompt-manager/docs/README.md) |
| Skill Manager | Accepted skill files, exports and project connections | [Skill Manager](../skill-manager/docs/README.md) |

The [project site](https://projects.aixand.org/manager-suite/docs/) offers the same
product categories through its documentation selector.

## Maintainer and evidence references

[Release installation](release-installation.md), [installer builds](INSTALLATION.md),
[qualification](installer-validation.md), [release policy](releases.md),
[beta scope](beta-support.md), [provider policy](provider-versions.md),
[SDK backends](sdk-backends.md), [dependency notices](DEPENDENCIES.md), and
[publication](publication.md) remain source-owned references.
Detailed product specifications, historical decisions, fixtures and live
qualification records remain in each product even when not selected for the site.

## Connected applications

[Build complete SDK workflows](examples/connected/index.md) with Carrier and Undercurrent: requests, workers, checked artifacts, restart recovery and a live worker/reviewer. Start with [the isolated setup](examples/connected/connected-setup.md). Examples show SDK code, Mermaid diagrams and observed results; three include real terminal recordings on the project site.
