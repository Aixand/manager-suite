# Conversation Manager documentation

Use these guides to integrate and recover retained chat conversations. This set
describes the development branch and follows documentation standard 0.1.0.
Installed provider versions and running workers can expose narrower capabilities.

## Getting started

- [Install and run a first conversation](getting-started.md)
- [Read evidence after worker stop](examples/offline-lifecycle.md)
- [Offline approval and queue recovery example](examples/offline-controls.md)
- [Reference chat frontend](../examples/reference_chat/README.md)

## Architecture

- [Understand ownership and compatibility](architecture.md)
- [Own and stop worker processes](runtime.md)

## Lifecycles

- [Attach a frontend and recover startup](frontend-startup.md)
- [Recover retained conversations](recovery.md)
- [Read turns, events and transcripts](turns.md)
- [Answer approvals and questions](approvals.md)
- [Manage message queues](message-queues.md)

## Integrations

- [Provider SDKs, Agent Manager and resources](integrations.md)
- [Codex server isolation and sharing](native-sharing.md)

## Reference

- [Configure state and defaults](configuration.md)
- [Change session settings](settings.md)
- [Discover slash commands](slash-commands.md)

## Development

- [Develop and verify the package](development.md)
- [Qualify a live provider](live-qualification.md): opt-in procedures that can consume account allowance
- [Exact tool-boundary audit](tool-boundary-audit.md): why observed completion is not an execution barrier
- [Recorded Codex benchmark](codex-sharing-benchmark.json): historical Linux measurements, not a performance guarantee

The full [frontend reference](frontend-api.md), [provider reference](provider-reference.md),
[isolation evidence](codex-server-sharing.md), qualification guide, audit and raw benchmark remain source references rather
than selected publication pages. Their scope and evidence must travel with their
claims. See [publication selection](publication.json) for source-owned site inputs.

## Connected applications

[Build complete SDK workflows](../../docs/examples/connected/index.md) with Carrier and Undercurrent: requests, workers, checked artifacts, restart recovery and a live worker/reviewer. Start with [the isolated setup](../../docs/examples/connected/connected-setup.md). Examples show SDK code, Mermaid diagrams and observed results; three include real terminal recordings on the project site.
