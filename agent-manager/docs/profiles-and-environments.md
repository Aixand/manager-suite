# Profiles and native environments

A native environment is the stable execution identity: provider, immutable
`environment_id`, canonical home, ownership, Manager preferences and public native
login observations. The provider owns sign-in, credentials, refresh and logout.

A profile is a named alias with an immutable `profile_id` and an `environment_id`.
Names are unique without regard to case within a provider registry. Every profile
is equal; aliases point directly to environments, never to other aliases. Several
names can share one environment without creating directories or copying files.

```sh
agent codex profile new work
agent codex profile alias w work
agent codex profile rename work company
agent codex profile default company
agent codex profile delete w
agent codex profile duplicate company backup
```

`new` creates an environment and its first alias. `connect NAME DIRECTORY` connects
an existing directory, reusing the registered environment for its canonical path.
`alias NAME EXISTING_PROFILE` adds a name. `duplicate` atomically publishes a fresh
environment and copies Manager preferences only; native credentials, settings and
history are not copied. Profile creation never selects an implicit default.

All aliases share the environment's model/resource preferences, saved launch arguments, native login and
active-run guards. The explicit default is an alias UUID: rename preserves it,
deleting another alias leaves it alone, and deleting the selected alias clears it.
Deleting the default does not assign a replacement. CLI `start` can independently
choose a named environment by remaining allowance where its manifest enables
[automatic startup](configuration.md#automatic-startup); that choice never writes
the default. Explicit-default operations still require one to be set.

Rename and alias deletion acquire only a short registry lock. They remain available
while login, launch preparation or native sessions are running. Accepted work
captures environment and alias IDs once. Renaming, removing and reusing the old
name cannot redirect that work. Alias IDs are provenance; deleting one does not
invalidate an environment lease or reconnect. Run and native session records
retain environment IDs across process boundaries.

Removing the last alias leaves an unnamed environment with its native files intact:

```sh
agent codex environment list --json
agent codex environment show ENVIRONMENT_UUID
agent codex profile alias recovered --environment ENVIRONMENT_UUID
```

Explicit retirement is separate:

```sh
agent codex environment forget ENVIRONMENT_UUID --expected-revision sha256:...
agent codex environment purge ENVIRONMENT_UUID --expected-revision sha256:...
```

Both require all aliases removed, no active or uncertain managed runs and no
retained session references to the environment, history store or descendant files.
`forget` preserves native files. `purge` additionally requires a verified
Manager-owned home with no symlink ancestors and checks native Codex history
references. External and shared homes cannot be purged. A filesystem failure during
purge can leave a detached `purging-*` directory for manual recovery; it never
retargets an active environment.

Two revisions have separate meanings. Profile `revision` guards alias edits and
default selection. `environment_revision` guards native login/logout and SDK launch
preferences. Renaming an alias changes only its alias revision. Model/resource
changes affect the environment revision shared by all aliases. Environment inspection
returns that revision as `revision`. Resource bindings also retain their own existing
revision/digest contracts. CLI and direct SDK callers may supply expected revisions;
MCP mutations require them where declared. Status observations do not change
preference revisions. `status` accepts no mutation revision.

Provider capabilities include `profileAliases`, `multipleEnvironments`,
`maxEnvironments`, `duplicateProfile`, `nativeLogin`, `loginStatus`, `logout`,
`retainedSessionResume`, `crossEnvironmentResume` and its reason.
Antigravity has exactly one shared environment per registry and supports many names
for it. Extra aliases share the current OS user's native home/keyring; they do not
isolate accounts. Duplicate and additional isolated environments are unavailable.

Continuing through another alias of the same environment needs no cross-home
adapter. A different environment still needs a qualified provider-specific adapter.
Codex has the existing Linux/bubblewrap adapter under its native version and writer
constraints. Claude 2.1.316 and Copilot 1.0.105 have Linux
[file-history adapters](file-session-continuation.md) using the destination login
and original history. Their locks coordinate Manager runs; close standalone vendor
sessions before handing off. OpenCode 2.0.18 supports
[native conversation copies](opencode-continuation.md), with independent destination
history, revision ancestry and branch preservation. In-progress work may be omitted;
Manager performs no activity checks or waiting for this route.
Antigravity offers native terminal history selection; exact retained resume remains
unqualified. Cross-provider continuation is unsupported.

The registry and operational state are stored in
`~/.local/share/aixand/manager/agent/state/PROVIDER/`:

```text
state.json                              schema 3
  environments[environment_uuid]        home, ownership, created_at
  profiles[name]                        id, environment_id, created_at
  default_profile_id                    alias UUID or null
  models/resource bindings/login_status keyed by environment UUID
  runs                                  captured environment and alias IDs
  sessions                              origin_environment_id and native history
  session_routes                        optional destination UUID/native ID -> source ref
  opencode_continuation                 optional copy revision graph and transfer journals

native-continuations/                   immutable public file-adapter launch plans
.locks/                                 registry, environment and operation leases
```

Complete Manager-owned native homes are separate from registry state:

```text
~/.local/share/aixand/manager/agent/home/PROVIDER/ENVIRONMENT_UUID/
```

That directory is the provider's native home for the environment and contains
whatever credentials, settings, configuration, history and metadata the native CLI
creates. With a custom `--home DIR` or `AGENT_MANAGER_HOME=DIR`, the equivalent path
is `DIR/home/PROVIDER/ENVIRONMENT_UUID/`.

Resetting all providers removes these provider home and registry subtrees but can
preserve shared Manager preferences, definitions, services and non-provider state.
A factory reset removes the complete Agent Manager context. A specific provider
reset can independently restore that provider's bundled Manager configuration;
native default homes and provider CLI software retain their own confirmations.

Registry and provider manifest version 3 replace previous formats. Resource snapshots
use `agent-manager/resource-snapshot-5` with explicit environment identity. Older
registries, manifests and identity records fail clearly; there is no migration or
compatibility reader. Manager never silently rewrites or relocates old native files.
Keep a copy of older state and connect preserved homes to a fresh registry explicitly.
