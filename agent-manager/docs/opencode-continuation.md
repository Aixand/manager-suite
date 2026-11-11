# OpenCode conversation copies

OpenCode continuation can copy a conversation into another environment and resume
it with that environment's native login and configuration:

```sh
agent opencode sessions --json
agent opencode continue SESSION_REFERENCE --profile company
```

The manifest selects `cross_environment_resume = "opencode-session-copy"`.
Metadata-only capabilities expose `crossEnvironmentResume = true` and
`crossEnvironmentResumeMode = "copy"`, with version status `unchecked`. Provider
inventory uses its observed CLI version to disable the feature on a mismatch;
execution rechecks it. This adapter supports **OpenCode 2.0.18**, which is also
Agent Manager's fresh-install recommendation. Ordinary CLI compatibility is broader.
Capabilities expose verification `pending`: the full native export/import check
still needs a host that permits native server listening. See
[terminal versions and capabilities](provider-versions.md).
Manager does not install or downgrade a provider as a side effect of continuation.

Selection and transfer are separate stages. Selecting retained metadata creates an
immutable request without exporting/importing or writing registry state. Explicit
preparation performs the journaled transfer and returns a plan containing both the
original reference and effective copy ID. CLI and SDK use the same coordinator and
OpenCode adapter; see [the continuation boundary](architecture.md#continuation-boundary).

## In-progress work

**Continue copies the history that native export makes available at that moment.**
OpenCode's native transfer filters unfinished assistant responses and running shell
or compaction messages. An in-progress turn, queued input or active tool execution
may not survive the copy. Export/import transfers projected history, not the running
process, its pending requests or its complete execution state.

Manager does not inspect turn activity, wait for idle, stop the source session or
ask for confirmation. The source terminal and unrelated sessions can remain open.
Choose an appropriate point in the conversation when you want a complete copy.

## Independent history and native accounts

OpenCode V2 stores saved credentials and sessions in its native SQLite database.
Manager invokes native `session export --standalone` in the source home and native
`session import --standalone --directory WORKSPACE` in the destination home. It
does not copy the database, read or write credential tables, manage tokens, or link
the homes. The selected destination's database remains its own account store.

The original workspace accompanies the copy.
Changes made afterward belong to that copy. There is no background synchronization
and a turn in the destination does not update the original home.

Native import preserves a supplied ID and refuses an existing session ID. Native
message IDs are also unique across a database. Manager therefore allocates fresh
native session and message IDs for each new copy, remapping structural message
references while preserving transcript text. Stable Manager conversation identity
connects these copies. Earlier native copies and independent branches are retained;
they are never overwritten or deleted to make room for a newer version.

Only the native projected transcript is transferred. Child session trees, provider
runtime state and external file/snapshot artifacts are not automatically cloned.
Root sessions with a staged revert and non-root sessions are refused because native
import cannot reconstruct their original context independently. Missing required
native data fails before terminal launch. Exported payloads larger than 64 MiB are
outside the current transfer limit.

## Revisions and branches

On copy-aware continuation, Manager exports the known available copies of that
conversation. A deterministic digest covers the projected messages, workspace and
semantic session fields. Native ID allocation, import update times and UI view times
do not change that digest. This detects native history changes made outside Manager
without treating timestamps as proof of which conversation is newer.

Manager records ancestry between observed revisions. An unchanged destination copy
is reused. If a newer descendant exists elsewhere, continuing an older copy follows
that descendant and creates a fresh destination copy when necessary. An unavailable
latest observed copy produces an error rather than silently falling back to older
history. Native failures retain their observed references for a later retry.

If two environments change independently, both revisions remain branches. Selecting
an exact native-copy session reference follows that branch. A logical conversation
ID with multiple heads reports the branch references to select; Manager does not
merge or replace one automatically. A selected branch can be imported alongside a
different branch already in the destination.

`agent opencode sessions --json` exposes these fields after the first transfer:

| Field | Meaning |
| --- | --- |
| `conversation_id` | Stable Manager identity shared by related copies |
| `ref`, `native_id`, `origin_environment_id` | Identity and location of this native copy |
| `history_revision` | Last observed projected-history digest |
| `copied_from`, `copied_from_revision` | Source copy and imported checkpoint |
| `copy_status` | `current`, `stale`, `branch` or `unobserved` |
| `last_observed`, `copy_available` | When the native history was checked and whether that check succeeded |

The terminal picker displays environment, copy status and native ID. Discovery and
public capability inspection remain metadata-only and start no provider process;
copy-aware continuation performs the native exports. Status means **latest observed
history**, not a guarantee about disconnected homes. A copy that has never been
transferred has no revision metadata yet. Ordinary same-home native continuation
remains available without registering a conversation-copy graph.

## Recovery and storage

Registries optionally contain `opencode_continuation` with a nested
revision graph and transfer journal. Each transfer records the source
revision, destination environment UUID, reserved native ID and expected message
identity before native import begins. The registry contains references, hashes and
public metadata, not exported message bodies or credentials. Private temporary
export/import files are cleaned up after the command.

After native import, Manager exports the reserved destination ID and verifies its
history digest and message identities before publishing the destination mapping.
If a process or registry write fails after native import commits, retrying the same
transfer verifies and adopts that committed copy. Discovery between attempts does
not prevent recovery. A conflicting or incomplete import is retained for inspection
and is never overwritten. Retained references/journals prevent environment retirement
from discarding their identity. Profile rename, alias deletion and name reuse keep
the captured environment UUID and its copies intact.

CLI `continue` and SDK `continue_session()` select copies across environments.
Managed raw `--session`/`resume_id` selection of a copy already in the selected
environment also uses this tracking. The selected branch retains its native workspace. Queued launches revalidate their captured copy, environment,
version and native metadata before submission, without checking turn activity.
Continuation uses explicitly connected native environments.

## Verification

`tests/test_opencode_continuation.py` exercises native-command doubles with separate
SQLite account/history stores. It covers copy reuse, returning newer history,
standalone edits, branches, collisions, alias churn, workspace preservation,
source activity independence and interrupted publication recovery. The native double
enforces both session and message ID collisions.

`python tests/manual/opencode_continuation.py` runs the production adapter with the
actual pinned CLI, disposable homes, synthetic messages and native title updates.
It makes no model calls or real logins. The development sandbox permits version/help
inspection but denies the native server's local `listen` operation with `EPERM`;
therefore the full native transfer/terminal route has not passed a host check here.
Authenticated model turns and cross-platform native execution remain unverified.

Native references: [session commands](https://dev.opencode.ai/v2/docs/cli/commands#session),
[account storage](https://dev.opencode.ai/v2/docs/cli/providers#storage), and
[the pinned transfer implementation](https://github.com/anomalyco/opencode/blob/v2.0.18/packages/core/src/session/transfer.ts).
