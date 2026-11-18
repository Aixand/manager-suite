# Troubleshoot Manager Suite

Diagnose setup, stale revisions and uncertain work while preserving existing identities and user content.

## A command or import is missing

Activate the environment that installed the selected manager. The root environment and each package-local environment are separate. Verify with `python -c "import sys; print(sys.executable)"`, then run that manager's `--help`.

For package-local setup, pass `--local` only when the declared sibling sources are available. Root development uses the five manager sources with published external dependencies. Check the exact path and package manifest before rerunning [installation](getting-started/installation.md).

## Old resource or server commands fail

Use Conversation Manager instead of the retired Server Manager. Use the owning Prompt, Skill, or MCP manager instead of retired Agent resource commands. Do not point a new store at an old store and assume automatic conversion. Existing stores are not moved or adopted automatically.

## A revision or preview conflicts

Read the current manager state and compare it with your original intent. A revision guard means the observed content changed. Regenerate and review a preview before applying another write; do not discard user edits to make a stale plan succeed.

Skill project destinations must be explicit. An unrelated existing directory is a conflict, not permission to overwrite it. `--adopt` only claims identical content under its documented rules.

## An MCP definition exists but the tool is unavailable

A saved definition is not a running connection. Check its installed executable, environment variable references, resolved project selection, preparation state, and connector capabilities. New stores contain no implicit Sonar or Lore definitions. Resolution alone does not start server software.

## A provider is listed but cannot start a chat

Conversation discovery reports implemented support rather than successful sign-in or model entitlement. Inspect the selected native environment with Agent Manager. Check the backend's current capability report and original startup operation before retrying.

## Work continues after closing a window

Frontend disconnection does not terminate a retained conversation runtime. Inspect the saved reference or operation. Use conversation cancellation/closure for the intended scope, or explicit startup cleanup when termination is required. Shared OpenCode runtime cleanup can stop multiple chats.

For Agent Manager execution, inspect the retained `run_id` and original Undercurrent runtime. Do not launch a second process merely because a response was lost.

## Optional execution cannot find Undercurrent

The base managers do not install or start a daemon. Agent Manager's async execution SDK needs the SDK package, matching native binaries, and a running daemon. Confirm those prerequisites through [Undercurrent installation](https://projects.aixand.org/undercurrent/docs/installation/). Carrier's messaging service is separate; see [Carrier operations](https://projects.aixand.org/carrier/docs/guides/operations/).

## Preserve data before maintenance

Stop the processes that own the data before backup or software changes. Keep databases, native provider homes, accepted resource stores, configuration, and receipts separate from versioned software. Inspect the selected product's current migration and installer contract before an upgrade. Software removal does not authorize data deletion.
