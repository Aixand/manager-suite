# Application and workflow integration

Use each manager independently or combine their public APIs in your application.
Resource managers own accepted content, user edits, reconciliation, and immutable
revisions. Integrating applications own workflow scheduling and delivery.

| Component | Owns | Integration reference |
| --- | --- | --- |
| Agent Manager | Profiles, environments and native execution | [Python SDK](../agent-manager/docs/python-sdk.md) |
| Conversation Manager | Retained conversations, startup, attachment, messages, recovery | [Frontend API](../conversation-manager/docs/frontend-api.md) |
| Prompt Manager | Owned prompts, rendering, captured content | [Python SDK](../prompt-manager/docs/python-sdk.md) |
| Skill Manager | Owned skills, validation, export | [Python SDK](../skill-manager/docs/python-sdk.md) |
| MCP Manager | Definitions, imports, installation, connector operations | [Runtime connectors](../mcp-manager/docs/connectors.md) |

## Prepare application resources

Applications compose resource managers with Agent Manager environment targets.
Declare the dependencies needed by your application; installing an extra does
not sign into a provider or start a daemon. See
[application resources](application-resources.md) for composition and
[resource imports](resource-imports.md) for source and installation ownership.

Keep resource identity separate from execution-account routing. Capture immutable
content for work that must survive retries, and reuse the retained request rather
than resolving mutable defaults again. Resource managers own content capture;
the external workflow host owns submission and retry policy.

## Retain conversations

Use Conversation Manager's frontend API to start or attach a conversation.
Persist its reference and operation ID so recovery does not depend on a current
profile alias. Closing a client and explicitly stopping owned work have different
effects; follow the [startup recovery contract](../conversation-manager/docs/frontend-api.md#startup-recovery).

See [installation](INSTALLATION.md) for software ownership and packaging.
