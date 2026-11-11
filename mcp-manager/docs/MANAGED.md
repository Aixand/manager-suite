# Owned MCP definitions

MCP Manager 0.1.2 owns immutable configurations and publisher reconciliation.
Agent Manager 0.1.2 attaches them without importing, updating, installing, preparing,
or reconciling at launch. These APIs work without Suite, Agent Manager or a backend.

```python
from mcp_manager import Manager
m = Manager()
status = m.ensure_registered("docs", {"command": "docs-mcp", "args": [],
    "env_vars": ["DOCS_TOKEN"]}, server_id="default", publisher_revision=12, version="1.4")
accepted = m.resolve_managed("docs", "default")
```

Call ensure once during consumer startup (and explicitly again to refresh).
Identical registration is idempotent. Newer nonnegative integer publisher revisions
auto-activate only when the copy has no edits. Customized copies keep their accepted
configuration and expose `reconciliation_required`. Unversioned changed content
requires review. Older revisions are ignored; reusing a revision for different
content conflicts. Display versions do not establish ordering. Pausing tracking
retains the copy and prevents subsequent registration from changing it. Invalid
incoming definitions retain accepted content and report `sync_error`.

Each state has an active digest, publisher baseline, incoming publisher snapshot,
guarded state revision and immutable history. Definition objects live in
`objects/`, active state in `managed/PUBLISHER/SERVER.json` and state history in
`managed-history/PUBLISHER/SERVER/`. Writes publish immutable objects/history before
atomically switching active state under the managed lock. Read-only inspection of
an absent store writes nothing. Accepted copies do not require original publisher
files. Commands, external scripts and server software are referenced, not copied;
environment values are resolved at execution and are never stored by these APIs.

```python
edited = m.save_managed("docs", "default", {"command": "docs-mcp", "args": ["--local"]},
    expected_revision=status["revision"])
preview = m.preview_reconciliation("docs", "default")  # after an incoming update
m.reconcile("docs", "default", expected_revision=preview["revision"], take="managed")
```

Save replaces the complete definition. Reconciliation makes a structural three-way
proposal: scalar strings are values and arrays are atomic. JSON-pointer conflict
choices support `take=managed|publisher|baseline`, a replacement `value`, or delete.
A candidate must be valid and every conflict resolved before activation. Taking
managed content acknowledges the incoming baseline so the same update does not
reappear. Preview never mutates content; newer edits/updates invalidate its revision.
History/rollback preserve current publisher tracking and pending updates.

## CLI

```sh
mcp-manager managed ensure docs default --definition @server.json --publisher-revision 12
mcp-manager managed list
mcp-manager managed status docs default
mcp-manager managed resolve docs default --revision sha256:HISTORICAL_STATE
mcp-manager managed save docs default --definition @edited.json --expected-revision STATE
mcp-manager managed preview docs default
mcp-manager managed apply docs default --take managed --expected-revision STATE
mcp-manager managed apply docs default --resolutions @choices.json --expected-revision STATE
mcp-manager managed history docs default
mcp-manager managed rollback docs default OLD_STATE --expected-revision STATE
mcp-manager managed tracking docs default off --expected-revision STATE
mcp-manager managed inspect docs default --cwd /project --project /project
mcp-manager managed adopt vendor default --legacy-name docs --definition @publisher.json --publisher-revision 12
mcp-manager managed prepare sonar default --project /project --background
mcp-manager managed refresh sonar default --project /project --settings '{"resolve":true}'
```

`agent codex mcp managed ...` delegates to the same lifecycle using that Agent
Manager's sibling MCP store. Agent resource attachment uses the existing shared CLI:

```json
{
  "schema": "agent-manager/resource-request-2",
  "mcp": {"add": [{"target": {"kind": "managed", "publisher_id": "docs", "server_id": "default"}, "required": false}]}
}
```

```sh
agent codex backend preview --profile work --resources @request.json
agent codex run work --resources @request.json
```

Managed MCP targets support `store` (an absolute execution-machine directory) and
`revision` (a historical state token). Omitted stores resolve on the execution
machine. String targets and request-1 remain supported. Structured MCPs use
request-2 and binding-3; stored snapshots remain readable. Exclusions/overrides match
identity rather than revision. A launch captures accepted definitions, managed
status, and prepared generations before creation. Resume uses that captured content
even after stores/defaults change. A pending update does not block a usable accepted
copy. Configuration delivery is distinct from an observed live connection.

Suite exposes the shared managed editor, bounded comparisons, conflict choices,
history/restore, tracking, publisher registration, and preparation for registered
projects. Resource pickers and launch reports show pending updates. Registration
and saved edits emit catalog refresh events; external CLI changes appear on focus
and lightweight periodic library refresh. Connect advertises request-2 and resolves
on the device. Unsupported explicit optional managed MCPs can be omitted for a
request-1 companion with a retained report; required ones fail before dispatch.
Unknown capabilities never authorize silently replacing a required selection.

## Adoption and preparation

Local definitions remain user-owned until explicitly adopted.
Adoption imports their current content without inventing a publisher baseline.
When imported and publisher configurations differ, explicit review establishes the
baseline. There are no bundled Sonar/Lore definitions or initialization command.
Users install server software themselves and register definitions explicitly with
`managed ensure` or adopt their saved definitions with `managed adopt`.
`bootstrap_defaults()` is a no-op. Managed targets
are selected explicitly; existing profile assignments are not rewritten. Local
configuration commands cannot edit managed
state; managed edits always require the observed revision.

Managed Sonar/Lore preparation uses separate per-publisher/server project namespaces
with ordinary retained jobs in the same store. Preparation captures definitions and
requested revisions. The index fingerprint excludes startup/tool timeouts and cwd;
timeout-only edits reuse compatible generations. Manifests without the
index fingerprint keep their conservative full-definition comparison. Failed refresh
preserves the previous published generation. Reconnect preserves a running session's
selected configuration; live attach/detach is available only with backend support.
Preparation supports stdio servers; install executable dependencies separately.


Direct creation, public GitHub sources and retained import jobs are documented in
[Resource imports](../../docs/resource-imports.md).
