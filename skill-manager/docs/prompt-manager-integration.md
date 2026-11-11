# Prompt Manager lifecycle integration

Skill Manager requires `aixand-prompt-manager>=0.1.0b0,<0.2` and uses only its
public root exports. Prompt Manager has no runtime dependencies and never imports
Skill Manager. No Agent Manager, Suite, Undercurrent, Carrier, or Runner is needed.

`managed.Backend` implements the public `PackageBackend` and `PackageDelivery`
contracts. `_managed.Managed` is a compatibility adapter, not a lifecycle engine.
It translates SDK policy errors into `SkillError` and retains the existing
filesystem-error boundary. Skill-facing Manager APIs and CLI commands retain
their signatures and native result shapes.

## Responsibilities

Prompt Manager owns accepted-state validation and publication, publisher ordering,
customization preservation, three-way review/merge, conflict choices, guarded
edits, history, rollback holds, tracking, and bounded review views. Skill Manager
has no second merge or lifecycle implementation.

Skill Manager owns `SKILL.md` parsing, native validation, byte capture, binary and
executable preservation, directories, portable paths, containment, immutable
manifests/blobs, source validation, release references, and native export. It
marks binary payloads as atomic comparison values through the SDK contract.

Release and export callbacks run with the shared store lock held. Native helpers
do not reacquire that lock. Historical exports/publications select the exact
accepted snapshot. Existing immutable labels cannot be rebound, and conflicting
destinations are never overwritten.

Skill Manager owns GitHub discovery, imports, update policy, pinning, reference
switches, and retained jobs. Its optional `github-source` client acquires content.
Acquisition commits are bridged to the SDK's public validated record API. The caller holds the store
lock; `commit_record` rejects stale observations before publishing. Local publisher
registration cannot replace a GitHub source. Source metadata is validated before
reading/writing native state, and a source-only pending review can be acknowledged
even when its baseline and upstream digests match.

## Existing stores

The store remains at the existing Skill Manager home. `managed/`,
`managed-history/`, `objects/`, `blobs/`, and `releases/` retain their layout and
schemas. Native content and state digest algorithms do not change. Existing
history tokens, source/name/version references, and recorded publication provenance
remain valid. Inspection and repeated identical publisher registration do not
rewrite old state. Source files are never needed for historical resolution.

The native adapter disables new descriptive-summary fields so mutations preserve
the closed existing managed-state schema. The integration does not add tags or
archival fields, move skill data into a Prompt Manager home, or convert skill
packages to `pack.json`. Prompt Manager's prompt-store backup/check APIs do not
inspect this native skill store.

`tests/fixtures/pre_prompt_sdk_store.json` was captured with the original skill
lifecycle before delegation. Tests exercise read preservation, idempotent publisher
registration, historical selection, immutable releases, edits, reconciliation,
rollback, and native export. The ordinary skill suite continues qualifying binary
files, permissions, directories, stale guards, publication failures, and CLI use.

## Remaining dependencies

`aixand-lib-filesystem` supplies locking and Windows filesystem protection through a
small independent package. The `.locks/store.lock` location and native lock
protocol coordinate all writers sharing the store.
The optional `github` extra adds
the standalone GitHub source client. PyYAML still parses native metadata.
Repository development helpers still use Manager Suite's
parent scripts; installed operation does not require that checkout.
