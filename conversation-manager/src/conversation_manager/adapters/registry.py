"""Explicit, lazy runtime selection. Importing Conversation Manager needs no SDK."""

from ..models import ConversationError

SDK_PROTOCOLS = {"claude-agent-sdk": "claude-sdk", "antigravity-sdk": "antigravity-sdk", "copilot-sdk": "copilot-sdk"}


def adapter(core, prepared, services=None):
    if prepared.protocol == 'opencode-v2':
        from .opencode import OpenCodeAdapter
        return OpenCodeAdapter(core, prepared)
    if prepared.protocol == "codex-app-server":
        from .codex import CodexAdapter

        return CodexAdapter(core, prepared)
    if prepared.protocol in SDK_PROTOCOLS:
        if prepared.adapter_id != SDK_PROTOCOLS[prepared.protocol]:
            raise ConversationError("validation", "SDK protocol and adapter do not match")
        from .sdk import SdkAdapter

        return SdkAdapter(core, prepared, services)
    raise ConversationError("unsupported", "Unknown backend protocol")
