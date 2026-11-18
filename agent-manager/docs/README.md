# Agent Manager documentation

This documentation describes the development branch and follows documentation standard 0.1.0. Start with [first use](getting-started.md). Native terminal execution belongs here; programmatic chat belongs to [Conversation Manager](../../conversation-manager/README.md).

## Getting started

- [Getting started](getting-started.md)

## Architecture

- [Architecture](architecture.md)
- [Profiles and environments](profiles-and-environments.md)
- [Resource separation](resource-separation.md)

## Lifecycles

- [Selection](selection.md)
- [Execution recovery](execution-recovery.md)
- [File session continuation](file-session-continuation.md)
- [OpenCode continuation](opencode-continuation.md)

## Integrations

- [Launch context](launch-context.md)
- [Native environment lease](native-environment-lease.md)
- [Profile management MCP](profile-management-mcp.md)

## Reference

- [Storage](storage.md)
- [Configuration](native-configuration.md)
- [Provider versions](provider-policy.md)
- [Python SDK](sdk-execution.md)

## Development

- [Development](development.md)

## Runnable examples

- [Provider-free profile identity](examples/profile-identity.md)
- [Persistent native execution](../../docs/examples/persistent-agents.md)
- [Two independent reviewers](../../docs/examples/parallel-reviews.md)

- [Offline provider policy](examples/policy-snapshot.md)

## Retained source contracts and evidence

- [SDK v1 normative contract](sdk-contract-v1.md): signatures, errors, qualification, retry rules.
- [Live SDK qualification](live-sdk-qualification.md): explicit live consent and retained evidence.
- [Independent integration](manager-suites-integration.md): public boundary entry point.

- [Full configuration](configuration.md), [provider versions](provider-versions.md), and [detailed SDK guide](python-sdk.md): complete fields, procedures, and feature qualification.

These remain in source and are linked rather than republished as competing inventories. [Publication manifest](publication.json) selects the site pages and same-commit downloadable examples.

## Connected applications

[Build complete SDK workflows](../../docs/examples/connected/index.md) with Carrier and Undercurrent: requests, workers, checked artifacts, restart recovery and a live worker/reviewer. Start with [the isolated setup](../../docs/examples/connected/connected-setup.md). Examples show SDK code, Mermaid diagrams and observed results; three include real terminal recordings on the project site.
