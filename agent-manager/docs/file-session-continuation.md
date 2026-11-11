# Claude and Copilot continuation

Claude and Copilot can continue an existing conversation using another environment
of the same provider. Select a session reference and the destination profile:

```sh
agent claude sessions --json
agent claude continue SESSION_REFERENCE --profile company
agent copilot sessions --json
agent copilot continue SESSION_REFERENCE --profile company
```

The destination supplies its native login and settings. History stays in its
original home and keeps its native ID. The original workspace are restored. A profile alias rename or deletion does not change
the captured environment. Cross-provider continuation is unsupported.

CLI and SDK share the [continuation coordinator and adapter contract](architecture.md#continuation-boundary).
Selection captures a request without native changes. History routes and links are
prepared under the existing execution gate, then revalidated before launch. Native
transcript and link checks remain in the provider implementations.

## Supported native versions

These adapters currently require Linux and these exact CLI versions:

| Provider | CLI | History adapter |
| --- | --- | --- |
| Claude Code | 2.1.316 | Absolute original transcript path |
| GitHub Copilot CLI | 1.0.105 | One managed session-directory symlink |

Another version is refused before publishing a route or creating a link. This
qualification is separate from the general installation policy: a CLI may be
supported for ordinary profile use without being qualified for this handoff.
Agent Manager recommends the versions above for fresh installs. Existing compatible
installations are kept; changing versions is explicit. Metadata-only
`crossEnvironmentResume` reports platform/adapter availability with an `unchecked`
version status. Provider inventory incorporates the observed CLI version and
disables this feature on a mismatch; execution rechecks it. See
[terminal versions and capabilities](provider-versions.md). Another alias of the original environment uses the
ordinary native resume path and does not need a cross-environment adapter.

## Native storage

Claude's [resume argument](https://code.claude.com/docs/en/cli-reference) accepts an
absolute JSONL transcript path. Manager replaces the exact UUID resume selector with
the unique original `projects/*/UUID.jsonl` path while keeping the destination
`CLAUDE_CONFIG_DIR`. It does not copy transcripts or link either configuration home.
Missing, ambiguous or escaping source paths fail before launch.

Copilot's [native configuration directory](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference)
stores each session in `session-state/UUID/`. Manager creates only
`DESTINATION/session-state/UUID -> ORIGINAL/session-state/UUID`, so native events,
workspace metadata and session artifacts remain in the original directory. Native
login state, configuration and `providers.json` stay in the destination. An existing
local session or untracked link is never replaced or adopted.

Ownership is journaled before creating a Copilot link. An interrupted preparation
can recreate its missing owned link. A changed link or conflicting UUID route is
refused. Removing a profile alias leaves the environment and its native files intact.
Manager never reads credentials to construct either adapter, caches tokens or
exports/imports a replacement conversation. Inherited credential overrides are
filtered again by the native wrapper when the child starts.

Session discovery includes connected native homes. Claude discovery reads bounded
JSONL envelopes to find workspace metadata; it does not retain message bodies.
Copilot discovery reads bounded `workspace.yaml` identity/workspace fields without
opening event bodies. Already retained workspace references remain pinned.
Manager-owned destination links do not create duplicate origins. Duplicate native
IDs in distinct original stores require the full Manager session reference.

## Writer coordination

Close the conversation in standalone Claude or Copilot processes before handing it
off. Standalone vendor processes do not honor Manager's locks. Native probes showed
that the vendor CLIs can open one conversation concurrently; Manager cannot exclude
a new process launched outside it. Observable live Claude session PID markers are
also checked before handoff.

Within Manager, known sessions receive an exclusive durable lease keyed by provider,
canonical original history home and native ID, including across Manager registries.
Pending or uncertain launches retain their leases. Unknown native picker runs block
handoff from that history home until they end. A native wrapper holds an OS file
lock for the resumed child's lifetime, including termination cleanup. It validates
the source reference, route, link, version and final native arguments again before
execution. Forking, replacement sessions and home/workspace redirection are rejected.

Fresh Manager terminal launches receive an explicit native session UUID, allowing
their captured resources to be retained for later continuation. Native picker
launches remain available; newly chosen native IDs are discoverable from history.

## Registry and launch records

The optional registry mapping
`session_routes[environment_uuid][native_uuid] = original_session_reference`
preserves the canonical origin when resuming from a destination environment.
Raw managed `--resume UUID` calls resolve this route, restore the source workspace
and use its captured resources. A routed destination cannot rebind that UUID to
another original store.

Immutable launch plans under `native-continuations/` contain only public environment
and session metadata. Durable run receipts are revalidated before runtime submission
and again before native execution. Continuation uses explicitly connected native
environments.

## Verification

`tests/test_file_continuation.py` covers round trips, destination-account selection,
stable references through alias changes, workspace preservation, discovery,
writer leases, queued launch revalidation, collisions, link ownership and termination.

`python tests/manual/file_continuation.py both` uses disposable homes and synthetic
history with the actual qualified CLIs. Both production wrappers preserve the native
ID and original history inode and write an explicit native `/rename` back to the
source. Copilot also checks native SDK resume/write-back through the session link.
These checks make no model calls, use no real logins and do not change user homes.
Authenticated multi-account model turns have not been exercised.

OpenCode uses [native transcript copies](opencode-continuation.md) with revision and
branch tracking rather than sharing the original history store. Antigravity
retains one shared environment and native terminal history selection. Codex keeps
its existing Linux/bubblewrap history adapter and its own version/locking checks.
