# Changelog

## Agent Manager 0.1.3-beta

- Add a built-in `default` profile for each provider's native home, including automatic startup selection and session discovery.
- Preserve legacy profiles named `default` under an available `default-previous` name.

## Agent Manager and Conversation Manager 0.1.2-beta

- **Agent Manager:** report native sign-in progress, reject busy environments promptly, and detect sign-in commands that leave a profile signed out.
- **Conversation Manager:** add idle-worker sign-in handover checks that preserve active turns, approvals, queued messages, and background work.

## Agent Manager and Conversation Manager 0.1.1-beta

- **Agent Manager:** stop verified native Codex sessions on Linux and improve busy-session diagnostics.
- **Conversation Manager:** optional shared Codex servers, rebuildable transcripts, question-answer history, and clearer startup failure recovery.

## 0.1.0-beta

Initial beta candidate for five standalone managers:

- **Agent Manager:** agent environments, authentication profiles, and management MCP.
- **Conversation Manager:** retained conversations, streaming, reconnection, approvals,
  and cancellation through native Codex and OpenCode integrations.
- **Prompt Manager:** reusable prompts, composition, publisher updates, and snapshots.
- **Skill Manager:** reusable skills, publisher updates, validation, and export.
- **MCP Manager:** server definitions, imports, preparation, and connector operations.

Install managers individually or together. Each has its own software environment;
uninstalling preserves user data. Removing the collection preserves managers
installed independently.

See [beta notes](docs/beta-release-notes.md) for platform coverage and known limits,
and [installation](docs/release-installation.md) for setup.
