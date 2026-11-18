# MCP Manager documentation

Resolve and maintain stdio MCP definitions, then connect them through an explicit
consumer runtime. These pages describe the development branch and use the
structure and style of documentation standard 0.1.0.

| Reader task | Owning guide |
| --- | --- |
| Complete a first offline check | [Getting started](getting-started.md) |
| Find state owners and extension points | [Architecture](architecture.md) |
| Select a store, resolve names, edit safely | [Resolution and local revisions](resolution.md) |
| Review publisher updates and history | [Owned MCP definitions](MANAGED.md) |
| Import definitions and install generations | [Source imports](sources.md) |
| Prepare indexes and recover jobs | [Preparation lifecycle](preparation.md) |
| Implement inventory and reconnect | [Live MCP connector contract](connectors.md) |
| Call resources, prompts, and tools | [Connected operations](operations.md) |
| Apply authorization and limits | [Security and limits](security-and-limits.md) |
| Change and verify the package | [Development](development.md) |

## Runnable examples

- [Resolve without launch](examples/resolve-without-launch.md): no MCP SDK required.
- [Local connected operations](examples/local-operations.md): stdio fixture using the MCP Python SDK.

## Retained contracts and plans

The [product discussion](DISCUSSION.md) is a historical snapshot and proposal
record. Its former Agent delegation, assignments, native delivery, and dependency
attribution are superseded; they are not current implementation contracts. The architecture and source links above describe
current ownership. The [runtime schema](../src/mcp_manager/schemas/runtime.json)
and tests remain source references rather than copied symbol inventories.

[Package overview](../README.md) · [Suite development](../../docs/DEVELOPMENT.md)

## Connected applications

[Build complete SDK workflows](../../docs/examples/connected/index.md) with Carrier and Undercurrent: requests, workers, checked artifacts, restart recovery and a live worker/reviewer. Start with [the isolated setup](../../docs/examples/connected/connected-setup.md). Examples show SDK code, Mermaid diagrams and observed results; three include real terminal recordings on the project site.
