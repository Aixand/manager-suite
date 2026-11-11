# Provider SDK integration

Conversation Manager owns retained conversation APIs and optional provider SDK
adapters. Choose the guide for the operation you need.

Use these maintained entry points:

| Need | Owning guide |
| --- | --- |
| Retained conversations and optional dependencies | [Conversation Manager](../conversation-manager/README.md) |
| Start, attach, and recover a conversation | [Frontend API](../conversation-manager/docs/frontend-api.md) |
| Discover and execute provider commands | [Slash commands](../conversation-manager/docs/slash-commands.md) |
| Queue messages and steer active work | [Message queues](../conversation-manager/docs/message-queues.md) |
| Run provider checks | [Live qualification](../conversation-manager/docs/live-qualification.md) |
| Agent profiles and launch resources | [Agent Manager](../agent-manager/README.md) |

Discovery and implemented startup support do not establish account entitlement
or a successful model turn. Check the selected provider's capabilities and
qualification prerequisites before integrating it.
