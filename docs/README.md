# Manager Suite documentation

This documentation describes the development branch and follows documentation standard 0.1.0.

Start with the [package overview](../README.md) for setup and first use.

## Guides and references

- [Dependency and notice inventory](DEPENDENCIES.md)
- [Develop a package independently](DEVELOPMENT.md)
- [Install a release: hosted scripts or archives](release-installation.md)
- [Beta candidate notes](beta-release-notes.md)
- [Beta support and limits](beta-support.md)
- [Product installation](INSTALLATION.md)
- [Compose application resources](application-resources.md)
- [Validate installer artifacts](installer-validation.md)
- [Application and workflow integration](integration-architecture.md)
- [Provider version policy](provider-versions.md)
- [Owned creation and source imports](resource-imports.md)
- [Provider SDK integration](sdk-backends.md)

- [Selective CI and independent releases](releases.md)

## Managers

- [Agent Manager](../agent-manager/docs/README.md)
- [Conversation Manager](../conversation-manager/docs/README.md)
- [MCP Manager](../mcp-manager/docs/README.md)
- [Prompt Manager](../prompt-manager/docs/README.md)
- [Skill Manager](../skill-manager/docs/README.md)

The [application integration guide](integration-architecture.md) describes shared
ownership. Each manager's guides own its current API and storage contracts.
See [package development](DEVELOPMENT.md) for checks, tests, and builds.
