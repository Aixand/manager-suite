# Prompt Manager documentation

Own prompt content, review publisher deliveries, and render reproducible text or
messages. This documentation describes the development branch and follows
documentation standard 0.1.0. Runtime use requires Python 3.11+ and no model/service.

## Getting started

[Create your first prompt](getting-started.md), then run one of the complete
provider-free examples:

- [Transfer snapshots and recover history](examples/owned-workflow.md).
- [Review a publisher conflict](examples/publisher-review.md).
- [Compose a conditional pack](examples/compose-pack.md).

## Choose a reader task

| Task | Owning guide |
| --- | --- |
| Understand modules and change boundaries | [Architecture](architecture-guide.md) |
| Edit accepted content and reconcile updates | [Owned prompts](owned-prompts.md) |
| Compose templates and immutable releases | [Rendering](rendering.md) |
| Capture imports or retain external drafts | [Imports](imports.md) |
| Rename, archive, and duplicate | [Library organization](library-management.md) |
| Tag and search accepted metadata | [Search](search.md) |
| Integrate logical selections | [Integrations](integrations.md) |
| Adapt native package policy | [Package lifecycle](native-packages.md) |
| Use versioned public Python contracts | [Python SDK](sdk-reference.md) |
| Audit writes, integrity, and retry behavior | [Storage reliability](storage-reliability.md) |
| Recover a full store | [Backup and restore](backup-restore.md) |
| Set up, test, build, and maintain docs | [Development](development.md) |

## Retained source contracts

[FORMAT.md](FORMAT.md) owns exact pack schemas, template rules, output, and digest
contracts. [MANAGED.md](MANAGED.md) retains lifecycle and migration details.
[SDK_INVENTORY.md](SDK_INVENTORY.md) records API coverage and remaining work.
[PRODUCT_DISCUSSION.md](PRODUCT_DISCUSSION.md) retains product rationale and scope.
These source references are intentionally not selected for full site publication.
The [package README](../README.md) remains the repository entry point.

[Architecture detail](architecture.md), [Python SDK detail](python-sdk.md), and
[backend protocol detail](package-lifecycle.md) retain full source contracts.

## Connected applications

[Build complete SDK workflows](../../docs/examples/connected/index.md) with Carrier and Undercurrent: requests, workers, checked artifacts, restart recovery and a live worker/reviewer. Start with [the isolated setup](../../docs/examples/connected/connected-setup.md). Examples show SDK code, Mermaid diagrams and observed results; three include real terminal recordings on the project site.
