# Profile management MCP

Local administration also exposes `get_configuration`, `get_launch_arguments`,
`set_launch_arguments`, `get_native_instructions`, `set_native_instructions`, and
`check_readiness`. These use Agent Manager's headless configuration services and do
not launch an editor or perform onboarding interactions. Argument tools accept an optional `profile`; omission targets provider defaults.
Instruction tools use the default profile when `profile` is omitted. Setters
require `expectedRevision` from the matching dedicated getter and support durable
`requestId` receipts. Readiness uses cached sign-in/allowance by default and queries
native observations only when `refresh` is true. This local tool contract does not
require changes to an embedding application's own contract.

Install `aixand-agent-manager[mcp]` and run `agent-manager-mcp`. The stdio server shares
its validated management contract with the SDK administration dispatcher. There is
no Suite broker mode; integrations belong to the embedding application.
Use `--home` and `--config` to select state and provider declarations.

Tools: `list_providers`, `list_profiles`, `get_profile`, `create_profile`,
`connect_profile`, `alias_profile`, `set_default_profile`, `rename_profile`,
`duplicate_profile`, `delete_profile`, `check_profile`, `list_environments`,
`get_environment`, `forget_environment`, `purge_environment`, `get_budget`,
`list_budgets`, `get_models`, `get_integrations`.

`get_budget` takes `provider`, optional `profile`, and optional mutually exclusive
`refresh`/`cached` booleans; it returns `budget`. `list_budgets` takes the same
options without `profile` and returns `budgets`, one per environment. These are
read-only account-allowance tools; a refresh may contact the provider using its
native login. They accept no credential inputs and create no mutation receipts in
the local dispatcher. Capability metadata and cached inspection require no quota
query. Check snapshot and bucket freshness before displaying remaining allowance.
See [budget observations](configuration.md#budget-observations) for scope and
verification limits.

`get_models` takes `provider`, optional `profile`, optional project `cwd`, and
mutually exclusive `refresh`/`cached` flags. It returns `catalog`, the shared
`agent-manager/models-1` envelope. It is a read tool with no mutation receipt;
refresh may contact a provider through its native login. The standalone local
MCP dispatcher supports it.

`get_integrations` takes `provider`, optional `profile` and `cwd`, and mutually
exclusive `refresh`/`cached` flags. It returns `integrations`, the public native
integration snapshot. `get_budget` and `list_budgets` also accept `cwd` and `model`
for paying-connection selection. These are read tools and create no local mutation
receipt.

`alias_profile` requires a name and exactly one of `profile` or `environmentId`.
It creates another alias without copying files. `connect_profile` reuses a known
canonical home. Delete removes only an alias, including during active work. The
last alias's removal preserves an unnamed environment. Explicit environment
retirement refuses aliases, active/unconfirmed runs and retained session references;
purge also verifies Manager ownership and referenced native history.

Alias mutations use profile `revision` as `expectedRevision`. Environment mutations
use its own revision. Revisions are separate: alias rename cannot invalidate native
work accepted for an environment. Mutations may carry a UUID `requestId`. Completed
requests return their retained results; unconfirmed requests are not replayed.
Required fields, types and unknown inputs are validated by every dispatcher.

Antigravity supports one environment per registry, using the current OS user's
shared native home/keyring. Multiple names share it; additional isolated environments
and duplication are unavailable. Capability output exposes that distinction.

Native interactive login runs through `agent PROVIDER login PROFILE` or an embedding
application's retained native terminal. MCP exposes public status checking without
collecting challenges, passwords or tokens. Discovery starts no provider process;
`check_profile` explicitly invokes the native status command where supported.

Use `crossEnvironmentResume` and `crossEnvironmentResumeReason` to offer eligible
homes. Aliases of the original environment can continue without a cross-home
adapter. Claude 2.1.316 and Copilot 1.0.105 have Linux file-history adapters; actual
launch checks the native version. Metadata-only profile inspection exposes
`crossEnvironmentResumeCompatibility` with status `unchecked`, version requirements
and verification notes. Provider inventory reuses its observed CLI version to
disable unsupported continuation. See [terminal versions](provider-versions.md).
Their Manager locks coordinate managed writers;
standalone vendor sessions must be closed before handoff. See
[file-session continuation](file-session-continuation.md). OpenCode 2.0.18 reports
`crossEnvironmentResumeMode = "copy"` for native transcript transfer between isolated
environments. Copies keep their native account stores and develop independently;
revisions and branches are tracked. Unfinished work may be absent, without activity
checks or waiting. See [OpenCode continuation](opencode-continuation.md). Antigravity reports
`retainedSessionResume=false` and `nativeResumeMayCreateSession=true`; its native
terminal picker remains available, while structured retained resume is unqualified.

See [profiles and environments](profiles-and-environments.md) and
[configuration](configuration.md) for version-three contracts and paths.
