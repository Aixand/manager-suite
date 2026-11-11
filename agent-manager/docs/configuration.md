# Configuration

[README](../README.md)

Preferences live in `~/.local/share/aixand/manager/agent/providers.toml`; full provider manifests
live in its sibling `providers/` directory, and upstream service manifests in
`services/`. Bundled definitions remain defaults.
A local definition replaces the matching provider, retains bundled aliases, and
cannot enable unqualified cross-profile resume or change a shared-home provider
into an isolated-home provider.

```toml
[providers.codex]
launch_args = ["--model", "MODEL"]
runtime = "direct"
```

Allowed preferences are executable, runtime, native_home, run_args, launch_args,
app_server_args, detection_profile, acp_command, labels, and metadata.
Carrier configuration is not accepted by Agent Manager. Configure Carrier through
Carrier's own adapter.

Manage saved native arguments through the CLI:

```sh
agent codex config args add -- --yolo
agent codex config args add --profile main -- --model MODEL
agent codex config args list --profile main
agent codex config args remove --profile main -- --model MODEL
agent codex config args clear
```

Each action accepts `--json`. Without `--profile`, mutations update the provider's
`launch_args` preference in the selected providers TOML file (`--config`, then
`AGENT_MANAGER_CONFIG`, then the default path). Profile-scoped arguments live in
Manager's environment registry; aliases share them, rename retains them, and
duplicate copies them to the new environment. Listing shows only the selected
scope. Clearing profile arguments retains inherited provider defaults.

Arguments remain ordered literal argv tokens: provider `run_args`, provider
`launch_args`, profile arguments, then per-launch model selection and forwarded
arguments. Repeated options retain native CLI behavior; Manager does not merge
option/value pairs. `add` appends, and `remove` deletes all exact contiguous matches
of the supplied token sequence. Include an option's value to remove the whole pair.
Ordinary table edits preserve unrelated comments; multiline or inline tables may
be reformatted while preserving all unrelated configuration values.

Saved arguments affect native terminal launches (`run`, `start`, `continue`),
including detached terminal launches. Login, budget/model probes and app-server
configuration remain separate. They do not rewrite native profile settings.
Cross-profile continuation uses the execution environment's arguments and native
configuration; existing continuation argument restrictions still apply. The
conversation supplies its original history and captured resources.

## Native instruction editing

```sh
agent codex config instructions edit
agent codex config instructions edit --profile main
agent codex config instructions show --profile main
agent codex config instructions path --json
```

Without `--profile`, these commands edit the default profile's native instruction
file. Use `--profile` to select a native environment explicitly. Aliases share
files. Codex selects a nonempty `AGENTS.override.md` before `AGENTS.md`; Claude,
Copilot, OpenCode and Antigravity use the paths declared by their provider
manifests. Antigravity's native file is shared because its native environment is
shared. The editor uses VISUAL, then EDITOR, then a terminal picker, and commits
through a revision-checked draft. Failed edits retain the draft.
Instructions can be saved while native sessions are running. Saves lock only the
instruction file and reject concurrent changes made after the draft was opened.
Running sessions may continue using instructions they already loaded.

Provider-wide instruction distribution has been removed. Launching an agent does
not synchronize or remove marked sections. Existing native files and the old
`native-instructions` directory remain untouched; migrate their contents explicitly
if desired. No profile/default means the editor asks you to select an existing
profile through an actionable error rather than creating shared guidance.

## Effective configuration and diagnostics

`agent PROVIDER config show --profile NAME --effective --json` returns
`agent-manager/configuration-1`: argument layers and their combined order, explicit
model selection, instruction source paths/revisions, and normalized profile resource
defaults. Contents of native instruction files are available separately with
`config instructions show`. Vendor-owned project/global configuration is not expanded;
`native_defaults_resolved` is false. A null native model means its default is unknown,
not that the provider has no model.

With `--effective` and no `--profile`, the configured default profile is used when
one exists. Otherwise the response explicitly shows provider defaults only; it
does not query allowance or choose a profile implicitly.

Argument writes accept `--expected-revision`, using the revision from
`config args list --json` for the same scope. Instruction edits accept the revision
from `config instructions show --json`. These scope/file revisions are distinct
from the aggregate effective-configuration revision.

`agent PROVIDER doctor [--profile NAME] [--refresh] [--json]` returns
`agent-manager/readiness-1`. It distinguishes missing/disabled installations,
missing profiles, verified signed-out state, unchecked or unsupported login probes,
cached allowance freshness, general CLI compatibility, and feature-specific pins.
Without `--refresh`, it probes only the CLI version and reuses login/allowance
observations. `--refresh` explicitly queries native status and allowance. Neither
mode starts a model turn or performs native login. A structurally ready profile
can still have unknown login or unavailable allowance; these are separate findings.
Inspection exits zero when diagnostics completed; automation should inspect `ready`
and `issues`. Onboarding and startup reuse the same inexpensive setup check and
native login observation service.

Long-lived Python clients can call `refresh_configuration()` or request
`configuration(refresh=True)` (the default). Refresh updates the resolved provider
settings for subsequent work. Direct, SDK terminal and backend launches capture
their provider configuration in a task/thread-local scope, so refresh does not
change an in-flight launch's executable or saved provider arguments. Running native
processes retain their original launch configuration.

## Declaring native instruction capabilities

Native instruction paths belong to provider manifests, including custom providers:

```toml
[terminal.instructions]
strategy = "home"
filename = "AGENTS.md"
overrides = ["AGENTS.override.md"]
```

`home` resolves filenames within the selected environment. `opencode-config` uses
the native OpenCode configuration directory and requires the OpenCode home strategy.
`global` requires an ordered `paths` array of absolute or home-relative Markdown
paths. It selects an existing path, or the first path when none exist. Global
paths do not accept `overrides`. Filename and override entries must be distinct
Markdown basenames; path traversal and duplicate entries are rejected. Omitting
this table explicitly leaves native instruction editing unsupported. Provider
capabilities expose this policy as `nativeInstructions`. Global onboarding lists
all configured provider definitions; an absent custom installer is reported rather
than silently choosing another installation recipe.

## Manifest version 3

```toml
schema_version = 3
id = "example"
label = "Example"
description = "A native CLI with profile-directory support"
aliases = ["ex"]

[terminal]
executable = "example"
runtime = "direct"

[terminal.home]
strategy = "environment-directory"
environment = "EXAMPLE_HOME"
default_path = "~/.example"
binding = "environment"
settings_scope = "environment"
history_scope = "environment"

[terminal.login]
args = ["login"]
# Optional; omit commands the CLI does not support.
status_args = ["login", "status"]
logout_args = ["logout"]

[terminal.sessions]
strategy = "native-picker"
resume_args = ["--resume"]
cross_environment_resume = "disabled"
# Optional explanation exposed in profile and provider capabilities.
cross_environment_resume_reason = "An adapter for another native home is not qualified."

[terminal.models]
strategy = "native-argument"
```

Supported home strategies: environment-directory, opencode-xdg, native-shared.
Supported session strategies: codex-transcripts, opencode-database, native-picker,
native-latest. These select built-in implementations; adding a genuinely new storage
or continuation mechanism requires adapter code. A manifest cannot safely turn an
unimplemented mechanism on by declaring support.

`cross_environment_resume_reason` is an optional single-line explanation for a disabled
capability. A local definition without an explanation inherits the bundled one when
available. CLI profile output, SDK profile data and MCP provider/profile responses
expose `crossEnvironmentResume` and `crossEnvironmentResumeReason` in their capabilities. An
enabled capability has no disabled reason; native launch checks still apply.

Native login args may be empty when sign-in happens inside the interactive CLI.
Missing status support returns unknown/unsupported; Manager never infers login from
credential-file presence. Status command output is never persisted or relayed;
only a small public observation is retained. A zero login-command exit code records
command completion, without claiming that model access was verified.

Native credentials stay inside their provider-owned storage. There are no manifest
credential-file lists, token transfer strategies, account bindings, or recovery modes.

## Model discovery

Model selection settings and model discovery have separate manifest tables:

```toml
[terminal.models]
strategy = "codex-config"

[terminal.models.discovery]
adapter = "codex-models"
versions = ["0.162.1", "0.164.0", "0.166.0"]
timeout = 30
cache_ttl = 86400
```

Fixed adapters are `codex-models`, `opencode-models`, `claude-models`,
`copilot-models`, and `antigravity-models`, bound to their corresponding providers.
Omitting discovery disables it. Exact CLI versions gate the reviewed protocol;
these versions have synthetic fixture coverage, not live account qualification.
Timeout is 1–120 seconds and model TTL is 1–86400 seconds, default 30 and 86400.
There are no custom Python imports, credential paths or arbitrary commands.

| Provider | Metadata source | Versions covered by fixtures |
| --- | --- | --- |
| Codex | Native app-server `model/list`, with bounded pagination; no thread/turn | 0.162.1, 0.164.0, 0.166.0 |
| OpenCode | Private native server `/api/model`, selected environment and project | 2.0.18 |
| Claude | Native SDK control initialization without a user message; labeled alias fallback when initialization omits models | 2.1.302, 2.1.316 |
| Copilot | Native protocol 3 `models.list`, without session creation | 1.0.103, 1.0.104, 1.0.105 |
| Antigravity | Native `agy models`, shared native environment | 1.4.8 |

`agent PROVIDER models --profile NAME --json` and `Client.models(...)` return one
`agent-manager/models-1` envelope, rather than a bare list. It includes `provider`,
immutable `environment_id`, current `profiles`, canonical `cwd`, `source`,
`adapter_revision`, `cli_version`, `source_revision`, `observed_at`, `checked_at`,
`inspected_at`, `expires_at`, `next_check_at`, `refresh_due`, `cached`, `status`,
`models`, and a fixed-message `error`. Timestamps are Unix seconds.

Each model has its exact native `id`, `name`, `description`, underlying
`provider_id`, `kind` (`model`, `alias`, `preset`), optional concrete `resolved_id`,
`availability` (`listed`, `restricted`, `unknown`), nullable `default`, capabilities,
limits, variants and pricing. Capabilities currently normalize tools, vision and
reasoning; limits normalize context and output tokens. Variants retain native IDs,
category, label and known default. Pricing preserves tiers and explicit units;
missing currency/billing-unit metadata stays null. Copilot token prices whose unit
is absent are retained with `unit: null` and their reported billing batch size.
Null means unknown; an empty array means a known empty list. Native settings,
headers, request bodies and errors are never copied into snapshots.

OpenCode 2.0.18 variants retain the IDs from its native variant objects. Tool
support and vision come from its capability flag and image input modality;
reasoning support stays unknown because the catalog has no corresponding flag.
Context pricing thresholds retain the native tier size.

OpenCode terminal launches use the full-screen entry point and `--standalone`.
The selected model is merged into `OPENCODE_CONFIG_CONTENT` after managed MCP and
instruction overlays; native profile and project files are not rewritten. The
root model default accepts `provider/model`, while `#variant` needs an in-app
selection or an explicit Mini launch. Full-screen resume keeps the saved session
model; an explicit resume model override is rejected instead of being ignored.
`agent opencode run NAME -- mini` and `agent opencode run NAME -- run "PROMPT"`
retain their native model flags. See the native [CLI](https://opencode.ai/v2/docs/cli/)
and [model default](https://opencode.ai/v2/docs/config/#model) documentation.

Listed models do not guarantee account access. Claude's initialization and alias
fallback do not verify entitlement. Zero token prices or a zero request multiplier
do not establish unlimited subscription allowance. `--free` selects listed models
with known zero advertised USD token prices only.

Snapshots live under
`~/.local/share/aixand/manager/agent/state/PROVIDER/models/ENVIRONMENT_UUID/CONTEXT_HASH.json`.
Aliases share snapshots; project directories have separate contexts. Executable,
adapter, SDK and relevant configuration metadata participate in source revision.
A changed revision cannot reuse an earlier snapshot. Manager login/logout attempts
and environment retirement discard catalogs; alias renames and ordinary session,
token and budget activity do not invalidate them. External native login changes
may require explicit refresh when no public change can be detected.

The default rolling refresh interval is 24 hours, checked on demand by model reads
and integration reads that need a model route. Account allowance reads do not
trigger model discovery. There is no background scheduler. Success and attempt timestamps are
separate: temporary failures retain matching old data as visibly stale, without
advancing its observation or expiry. Failed attempts, including initial failures,
wait one daily interval before another automatic query. Manual `--refresh` always
retries. Authentication, version and format failures discard old model data.
Model failures do not suppress valid budget observations.

`models --refresh` forces only model discovery. `budget --refresh` forces budget
inspection; account-budget inspection does not query models. `--cached` never starts either
native probe. Explicit `run PROFILE` and `continue SESSION --profile NAME` do not
refresh these catalogs. Cache files are atomic private JSON, limited to 4 MiB;
per-context leases coalesce queries, with at most 64 project files per environment.
Backend catalogs consume the same snapshot and reject expired model snapshots;
Codex dependent setting choices use its preserved native model variants. SDK
pre-session catalogs retain built-in commands, model selection, tool approval and
Claude mode choices. Session-dependent SDK metadata is inspected after opening a
session rather than launching a throwaway conversation for discovery.

## OpenCode integrations

Integrations are native OpenCode connections inside a profile's environment.
Manager does not add saved-auth objects, credential CRUD, credential transfer or
cross-provider account bindings. OpenCode owns activation, sign-in and refresh.

```toml
[terminal.integrations]
adapter = "opencode-integrations"
versions = ["2.0.18"]
timeout = 30
cache_ttl = 300
# Optional restriction; omit to discover all declared services automatically.
# services = ["openai", "openrouter"]
```

This optional table defaults to disabled for other definitions. The reader is
OpenCode-only; versions must be unique exact CLI versions. Timeout is 1–120 seconds
and TTL is 1–300 seconds, default 30 and 300. Shared service definitions supply
native integration IDs, packages, routing endpoints and authentication policies.
An explicit `services` array restricts discovery; `services = []` disables all
connection queries while retaining public native inventory. It cannot contain
duplicates or unknown references.

CLI provider identity and upstream service identity are separate. Codex declares
`service = "openai"` at the top of its provider manifest; Claude, Copilot and
Antigravity reference Anthropic, GitHub and Google respectively. OpenCode has no
single upstream service. OpenRouter needs no corresponding CLI provider.

For example, the shipped OpenAI service definition is:

```toml
schema_version = 1
id = "openai"
label = "OpenAI"

[discovery.opencode]
integration_ids = ["openai"]
packages = ["@opencode/ai/providers/openai"]
endpoints = ["https://api.openai.com/v1", "https://chatgpt.com/backend-api/codex"]

[authentication.oauth]
budget_kind = "subscription"
budget_adapter = "openai-native"

[authentication.key]
budget_kind = "api-billing"
budget_adapter = "openai-native"
```

Place a complete override at `~/.local/share/aixand/manager/agent/services/openai.toml`, or add
a new service TOML file in that directory. With `--config`, the services directory
is a sibling of the chosen preferences file. Overrides replace complete service
definitions. No configuration files are written automatically. Missing discovery
declarations disable matching for that service. Duplicate service IDs within a
directory and overlapping native integration IDs across services are errors.

New services can declare authentication types and a `budget_kind` of subscription,
api-billing, credits or unknown. Omit `budget_adapter` to recognize a connection
while leaving its allowance unknown. Existing budget readers are fixed built-in
protocols: `openai-native` reads ChatGPT OAuth Codex quota and recognizes API-key connections;
`openrouter-key` supports OpenRouter key observations. Definitions cannot import
code, execute commands or redirect those readers' quota requests. A genuinely new
budget protocol requires an adapter. Endpoint lists constrain recognized routing;
they are not arbitrary quota request URLs.

```sh
agent opencode integrations --profile work --json
agent opencode integrations --profile work --refresh --json
agent opencode integrations --profile work --cached --json
agent opencode budget work --model openrouter/openai/MODEL_ID --cwd /project --json
```

`agent-manager/integrations-2` includes the provider, stable environment ID,
current profile aliases, canonical project directory, source revision and CLI
version, observation/check/inspection/expiry timestamps, cached/status/error,
`integrations`, `routes`, and the native `default_model`. Each integration has its
native ID/name, `recognized` and `reviewed` flags, `service_id`, related
`cli_providers`, `budget_kind`, public method types/IDs, public connection IDs/types,
known active connection, and a normalized budget. `connection_status` is selected,
none or unknown; a selected connection does not prove its login is valid.
Authentication prompts, connection labels,
URLs, native errors, headers, bodies, credentials and arbitrary metadata are
excluded. Unknown active selection stays null rather than guessing from ordering.
Routes preserve the actual native provider-to-integration-to-service mapping, reviewed
runtime/endpoint status, and model selectors from the independent model cache.
`recognized` means the native integration ID has a service declaration; `reviewed`
also requires a matching runtime package and endpoint on at least one native route.

Related CLI providers describe service compatibility, not account identity. An
OpenAI match may list Codex, but does not link its profiles, read their credentials,
or borrow their allowance. No cross-environment account equivalence is inferred
from service IDs, profile names or local connection IDs. Unknown integrations
remain visible without service or budget guesses. Model creators do not determine
the paying service; the actual native route does.

The short-lived private server uses `/api/integration`, then a bundled native
plugin RPC. `OPENCODE_CONFIG_CONTENT` adds that plugin for this process only;
existing inline JSON and native configuration are preserved. Inline content must
be a JSON object. It does not install a persistent plugin or rewrite profile files.
The bundled plugin is self-contained JavaScript with portable JSON-Schema RPC
definitions. It does not require `@opencode/plugin` in the user's configuration,
project, or Python installation, or a package installation during inspection.
The plugin uses OpenCode's active-connection resolver inside the OpenCode process.
Manager never requests `/api/credential` or reads the credential database. The
provider credential is neither returned to Python nor copied into Manager state.
The RPC exposes only public metadata and quota observations; it cannot perform
sign-in, activation, inference, purchases or arbitrary HTTP requests.

| Connection | Current observation |
| --- | --- |
| OpenAI ChatGPT/OAuth | Browser/device connections report Codex percentage windows and reset times through an isolated Codex app-server. |
| OpenAI API key | Recognized separately; API billing allowance unknown. |
| OpenRouter API key | Current-key spending cap, remaining amount, usage and applicable shared budgets, when reported. Account-wide credit balance remains unknown. |
| Other integration/runtime/method | Explicitly unreviewed for Manager budget handling. Native execution remains available. |

The OpenAI OAuth reader supports OpenCode 2.0.18's `chatgpt-browser` and
`chatgpt-headless` methods on the native `@opencode/ai/providers/openai` route to
`https://chatgpt.com/backend-api/codex`. OpenCode resolves the selected connection
and renews its token when needed. The bridge checks the native account header,
then passes only the access token and account ID over stdin to a temporary Codex
app-server using experimental `chatgptAuthTokens` authentication. It calls
[`account/rateLimits/read`](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt)
without creating a thread or turn. It checks the selected credential again after
the read; a changed connection, account or token discards the observation.

This reader requires an installed Codex executable. It uses `[providers.codex]`'s
configured executable and the qualified versions from Codex's budget manifest
listed in the [provider definition](../agent_manager/providers/definitions/codex.toml).
It does not install Codex or require a separate
Codex login. A missing or unqualified executable returns an explicit unavailable
observation. The child has a fresh temporary home, ephemeral credential storage,
bounded output and timeout, and disabled telemetry. Its home is removed afterward;
detected credential persistence rejects the result. Tokens never enter Python,
Manager caches, command arguments or reports. OpenCode keeps its stored login and
refresh token; a child refresh request ends the probe rather than creating a
second refresh owner. Native sign-in is needed again only when OpenCode cannot
renew its connection.

The common budget schema preserves each returned window's actual duration and
reset time. `primary` can be weekly; it is not assumed to mean a short window.
Only the general `codex` meter from this reviewed OAuth route is eligible for
percentage comparison. Extra, unfamiliar meter groups remain visible but exclude
the environment from ranking. Comparison uses the same selected model route in
every candidate, and reports relative remaining Codex allowance rather than
absolute tokens, model entitlement or a spending guarantee. Newer Sign in with
ChatGPT methods and OpenAI API-key billing remain unqualified. Modern ChatGPT
plan grants can have [app-specific limits](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery),
so a matching service or email alone never qualifies a different connection.

Authenticated quota retrieval needs its own live check. Fixture tests do not
establish browser OAuth, provider entitlement, or native platform support. Keep
run-specific allowance values and temporary-home observations in qualification
reports rather than using them as current account state.

## Budget observations

Account-budget capabilities belong to the provider definition, not the preference
file or an alias. A local definition beside `providers.toml` can declare:

```toml
[terminal.budget]
adapter = "codex-rate-limits"
versions = ["0.162.1", "0.164.0", "0.166.0"]
timeout = 20
cache_ttl = 300

[terminal.budget.selection]
strategy = "max-remaining"
buckets = ["codex:primary", "codex:secondary", "default:primary", "default:secondary"]
```

The built-in readers are `codex-rate-limits`, `claude-usage`, `copilot-quota` and
`antigravity-usage`; each is bound to its corresponding provider. Other definitions
default to `adapter = "disabled"`. A disabled reader may declare a `reason`.
OpenCode's provider-account reader is disabled; its reviewed native integrations
have separate connection-scoped observations described above.
Manifests cannot import reader code, supply arbitrary budget commands or name
credential files. Exact `versions` gate the reviewed CLI formats; changing this
list requires reviewing the native output and protocol. Timeout is 1–120 seconds,
and cache TTL is 1–3600 seconds. Bundled defaults are 20 and 300 seconds.

| Provider | Reader | Reviewed version gate |
| --- | --- | --- |
| Codex | Native stdio app-server `account/rateLimits/read`; no thread or turn creation | [Definition](../agent_manager/providers/definitions/codex.toml) |
| Claude | Native structured local `/usage` command, with tools, MCP and session persistence disabled | [Definition](../agent_manager/providers/definitions/claude.toml) |
| Copilot | Native headless stdio `account.getQuota`; no conversation creation | [Definition](../agent_manager/providers/definitions/copilot.toml) |
| Antigravity | Native structured `/usage` command in its shared home | [Definition](../agent_manager/providers/definitions/antigravity.toml) |

Each definition's `terminal.budget.versions` lists the exact reviewed versions.

The reader starts the selected executable with the environment's normal native
home and filtered launch environment. Provider login and refresh remain native.
Python receives no credentials; the OpenCode OAuth bridge described above passes
an access token between native processes only. Native local-command envelopes must
identify the usage command and report zero model turns; model text and token usage
are rejected as quota data. Probes have bounded output, a shared timeout across
version checking and retrieval, and subprocess cleanup. Reads do not retry, switch
native logins, poll in the background or change billing settings.

Use `agent PROVIDER budget PROFILE --json` for one environment, or `--all` for one
observation per environment, including unnamed environments. An omitted profile
uses the explicit default. A recent observation is reused until its TTL or a
reported reset expires; `--refresh` forces a native query and `--cached` starts no
provider process. Those options are mutually exclusive. Provider discovery and
ordinary profile listing remain metadata-only.

The `agent-manager/budget-1` response has `scope = "provider-account"`, stable
`environment_id`, current alias names in `profiles`, `source`, `observed_at`,
`checked_at`, `inspected_at`, `cached`, `status`, `buckets` and a sanitized `error`.
`observed_at` is when Manager received the last successful native result;
`checked_at` is its last native query attempt, and `inspected_at` is this inspection.
Neither timestamp promises that a vendor bypassed its own internal caches.
Each bucket preserves its ID, scope, unit, used/remaining/limit values, optional
remaining percentage, reset and window duration. Account allowance has
`kind = "allowance"`; a reported spending cap has `kind = "spending-cap"`.
Top-level `status = "ok"` describes a successful observation, not available
capacity: a successfully read bucket can still be exhausted or disabled.
Session token consumption and estimated session cost stay in the backend usage
interfaces and are never subtracted from an invented subscription total.

Unknown values remain null. A zero allowance, an unlimited entitlement, a disabled
bucket and unavailable data have distinct states. A 0/0 request entitlement is
unknown unless the provider supplies further allowance information. Reset expiry
marks an observation stale; it never fabricates a full allowance. Native credit
balances retain credit units. Claude's extra-usage USD amounts are converted from
reported cents; other currencies are not mislabeled as dollars. The reader only
observes spending caps and never changes or enforces them.

Snapshots are private JSON metadata under
`~/.local/share/aixand/manager/agent/state/PROVIDER/budgets/ENVIRONMENT_UUID.json`. Alias rename
and deletion preserve them; native login/logout attempts and environment retirement
discard them. Policy, executable and recorded-login changes invalidate the cache.
After changing an account directly in the native CLI, use `--refresh`: Manager does
not watch credential files to detect external account changes. Separate homes can
use the same account or organization allowance, so the API exposes no sum or
independent per-profile entitlement.

Temporary retrieval failures can retain a previous observation marked stale, with
its original observation time and a sanitized error. This failed-refresh state is
also retained for cached reads. Authentication, CLI-format and version failures
discard the previous observation. Cached input is validated before display; raw
provider output, messages, tokens and account identities are not retained.

Verification currently covers native-protocol doubles, parsing, units, missing and
unlimited values, timeouts, output bounds, alias churn, cache invalidation and the
CLI/SDK/local MCP paths. No real account quota queries or model calls were made.
Authenticated retrieval and native macOS/Windows behavior remain unqualified;
the public budget capability reports `verification = "fixture-tested"`.

Protocol references: [Codex app-server](https://learn.chatgpt.com/docs/app-server),
[Claude usage](https://code.claude.com/docs/en/costs),
[Copilot account quota](https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/usage-and-billing),
[Antigravity usage](https://antigravity.google/docs/cli/commands/usage/).
Claude 2.1.302's installed native usage-report schema was also inspected: null
limit rows do not contain its cached screen bars, and extra-usage amounts use
currency minor units. These structured command formats are version-gated.

## Automatic startup

`agent PROVIDER start` selects an execution environment once, then follows the same
native launch, resource and run-lease path as `agent PROVIDER run PROFILE`. It prints
the chosen profile and score to stderr. It never changes the configured default,
changes a native login or moves an already running conversation to another home.
`run PROFILE` and SDK launches retain their explicit/default selection rules and do
not query budgets to choose another environment. CLI continuation uses budgets to
order manual profile choices, as described below.

Bundled Codex, Claude, Copilot and Antigravity definitions use
`terminal.budget.selection.strategy = "max-remaining"`. Candidates are environments
with at least one profile alias; unnamed environments are excluded. Each environment
is inspected once, regardless of its alias count. Fresh cached readings are reused;
otherwise the native reader runs, with at most four environments queried in parallel.
Native observation times do not guarantee that a vendor refreshed its own cache.
After changing a login outside Manager, refresh the budget explicitly before `start`.

The score is the **lowest remaining percentage across applicable allowance windows**.
For example, short/weekly windows of 90%/10% score 10%; a second environment at
60%/50% scores 50% and wins. Counts are comparable only when their total capacity is
known. This ranks relative remaining allowance, not absolute tokens or messages
across different plans. All-applicable-window unlimited allowance ranks above finite
allowance. Ties prefer the configured default alias/environment, then a stable alias
name order. A default is not required for this strategy.

The selection table declares bucket-ID globs using `*` and `?`; only matching
`kind = "allowance"` buckets participate. Bundled policies use Codex's primary and
secondary coding windows, Claude's allowance windows, Copilot's
`premium_interactions` and Antigravity's `gemini-*`/`3p-*` groups. Optional paid
balances, spending caps and unrelated quota meters do not increase the score or
permit an exhausted base allowance to qualify. Codex's single-view response without
a limit ID is normalized as `default:primary`/`default:secondary`.

Providers can declare model-family globs without adding provider-specific ranking
code. For example:

```toml
[terminal.budget.selection.model_families]
opus = ["*opus*"]
sonnet = ["*sonnet*", "opusplan"]
```

Ranking considers explicit `--model`/`-m` arguments and Manager's saved launch model.
It excludes a model-scoped window only when the requested model and that window
belong to known, different families. Global windows still constrain every model;
unknown models, unknown scopes and native configuration defaults retain all matching
windows. Claude's `opusplan` includes both Opus and Sonnet constraints. Antigravity
still has only one shared environment; aliases cannot provide additional allowance.

An applicable exhausted, disabled, unknown or stale window disqualifies its
environment. A passed reset requires a refreshed observation; it does not imply a
full allowance. Failed refreshes are excluded even if an older reading looked
usable. If no candidate qualifies, interactive `start` explains the exclusions and
offers an explicit profile choice (or selects the sole profile), followed by native
sign-in when needed. Explicit choices treat allowance as advisory. Noninteractive
startup stops with the failure. Inspect
`agent PROVIDER budget --all --refresh --json` or use `run PROFILE` explicitly.

`agent onboarding` opens provider setup, and `agent PROVIDER onboarding` configures
one provider. Interactive `start` enters setup when installation, enablement or
profiles are missing. The TUI supports creating profiles, connecting native homes,
native authentication and launch. The first profile created there becomes default.

Before recording the native launch, Manager rechecks the selected environment's
preferences, observation age/reset times, cache identity and recorded login/executable
revision. A changed or expired selection requires retrying `start`; it never silently
launches another environment. Alias rename and removal cannot redirect the captured
environment ID. Quota can still change outside Manager after an observation, so this
is startup selection rather than an allowance reservation or spending guarantee.

Missing selection policy, or `strategy = "default"`, uses the explicit default
without querying budgets. OpenCode's separate `comparable-or-default` policy
requires its integration reader and nonempty bucket globs; it compares only
qualified connections with matching model routes, with the fallback described
above. A local provider definition replaces the bundled selection table.
`max-remaining` requires an enabled account reader and nonempty bucket globs.
Provider capability metadata exposes `budget.automaticSelection` as `max-remaining`,
`comparable-or-default` or `default`. Focused offline checks cover scoring, models, cache freshness, alias
identity, concurrency and the full CLI launch path. Real-account/native-platform
qualification remains deferred.

## Profile registry

The default state root is `~/.local/share/aixand/manager/agent/state`. Each provider has a
version-three `state.json` with an `environments` table keyed by immutable UUID and
a `profiles` table mapping names to alias UUIDs and environment references. The
explicit default is an alias UUID. Environment preferences and login observations
are keyed by environment UUID. Runs retain both identities; sessions retain their
origin environment and canonical native history location.

Managed native homes use
`~/.local/share/aixand/manager/agent/home/PROVIDER/ENVIRONMENT_UUID/`. Connecting a home
already registered in this provider creates another alias of that environment.
Rename and alias deletion never move homes or wait for active sessions. Deleting
the selected default clears it; another alias is not automatically selected.
Duplicate publishes a fresh environment, its preferences and alias in one registry
write, copying no native settings, credentials or history. Environment forget/purge
are explicit operations with separate guards. See [the lifecycle and revision
contract](profiles-and-environments.md).

CLI `--home DIR` and `AGENT_MANAGER_HOME` override the state root; managed native
homes then use `DIR/home/PROVIDER/ENVIRONMENT_UUID/`. Provider preferences
use `--config FILE` first, then `AGENT_MANAGER_CONFIG`, then the default path. A
selected preference file locates definitions beside that file, including when the
preference file itself is a symlink.

Reset has three scopes. `agent reset PROVIDER` removes one provider's state and
homes; `--reset-config` additionally removes that provider's preference table and
exact local manifest override so bundled defaults apply. Shared service definitions
remain because more than one provider can consume them. `agent reset --all-providers`
removes all provider state while preserving preferences, definitions, services and
shared Manager state. `agent reset --factory` removes the entire selected Agent
Manager configuration and state context. Interactive reset chooses the global scope
and confirms provider configuration, native default homes and CLI removal separately.

Registry and manifest inputs must use the supported formats. Native directories
are preserved and can be connected explicitly; connection selects the directory
without migrating its credentials or sharing it with another profile.

## Continuation limits

CLI continuation selects a session, then its execution profile:

```sh
agent codex continue
agent codex continue SESSION_REFERENCE
agent codex continue SESSION_REFERENCE --profile work
```

The first form lists sessions in the current workspace before asking for a profile.
The second finds the named native ID, full session reference or supported logical
conversation ID, then asks only for a profile. The third goes straight to the chosen
profile with no picker or budget query. Both the session and `--profile` are required
for noninteractive CLI use; missing interactive input fails before budget queries.
If discovery finds no sessions, the fallback asks for a profile, then opens the
provider's native history picker. Cancellation at either Manager screen launches
nothing.

The profile table has `Profile`, `Budget` and `Native home` columns. It reuses the
automatic startup reader, cache, bounded parallel inspection and manifest bucket/
model-family rules. Aliases remain separate choices with the same environment
observation; each environment is queried once. Unlimited allowance sorts first,
then fresh remaining percentages from highest to lowest, including `0% — exhausted`.
The configured default breaks equal scores, followed by alias name. Unknown, stale,
disabled or unavailable quotas appear after comparable readings with explicit labels.
An unsupported CLI budget interface displays `Unsupported CLI`. Providers without
an account reader or integration reader show `N/A` without native queries.
OpenCode uses the connection rules above, including the integration label and
mixed-route fallback order.

Budgets guide a manual choice. Every listed compatible profile remains selectable,
including exhausted or unknown allowance, and choosing it does not change the
configured default. Values are snapshots when the table opens; they are not polled
or reserved while the picker is open. A later quota change does not cancel an
explicit choice. Capture uses environment and alias IDs, so alias rename, deletion
or reuse cannot redirect the resume. Environment preference changes require a fresh
selection, with a final read-only check inside the normal native launch claim.

Model-scoped quotas use an explicit `--model` or the effective Manager launch model
when available. The picker does not guess a resumed model by reading conversation
bodies; when native resume defaults are unknown, all matching quota windows remain
constraints. Shared global windows still apply. SDK continuation does not open this
picker or query budgets; its existing explicit/origin environment behavior remains.

The session picker offers only eligible execution profiles. For providers without a
qualified adapter, that means a connected profile referencing the original history
home. A disconnected source can be reconnected under a new name without changing
its retained session reference. Explicit incompatible selections report the provider
reason and the home to connect before any native launch.

OpenCode V2 stores its saved credentials and sessions in the same native SQLite
database. Redirecting `OPENCODE_DB` to the original history would also select that
database's saved accounts. The `opencode-session-copy` adapter instead invokes native
session export/import into the selected environment's own database. It requires
OpenCode 2.0.18 and tracks related copies, observed content revisions and branches.
Equal copies are reused and newer histories create fresh native copies. Earlier
copies are preserved. Destination changes do not automatically update the original;
unfinished work may be absent from native export. Manager performs no turn-activity
checks, waiting or source termination. See [OpenCode continuation](opencode-continuation.md)
for storage, native-version restrictions, recovery and verification limits. Native
profile login remains provider-owned; Manager never copies credential tables.

Claude's [native resume interface](https://code.claude.com/docs/en/cli-reference) accepts
an absolute transcript path. On Linux, the `claude-transcript-path` adapter resumes
the original transcript under the destination `CLAUDE_CONFIG_DIR`, preserving its
native ID and writing back to the original file. It requires Claude Code 2.1.316.
The production wrapper was checked with a disposable native interactive `/rename`,
without real login or model calls. See [file-session continuation](file-session-continuation.md)
for version checks, writer coordination and storage details.

For retained Claude references, Manager requires an exact UUID and a unique native
`projects/*/UUID.jsonl` file inside the original home. Retained lookup checks paths;
discovery reads bounded envelopes for workspace metadata without retaining message
bodies or reading credential files. Missing, ambiguous or escaping paths fail before
native launch. Provider arguments that fork, rewind, replace or relocate that
conversation are rejected at final command construction. Native picker launches
remain available for sessions outside these retained-reference checks.

Claude's native credential scope also depends on its sign-in method. The provider
documents separate `CLAUDE_CONFIG_DIR` homes for claude.ai logins and stored API keys,
including directory-specific macOS Keychain entries. Keyless Console sign-in uses
Anthropic profiles outside that directory, so separate Manager homes do not isolate
those sign-ins. Manager leaves those credentials provider-owned; see
[Claude authentication](https://code.claude.com/docs/en/authentication).

Copilot's [native configuration directory](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference)
contains profile-owned login state, `providers.json`, and `session-state/UUID/`
history/workspace data. Manager leaves those files in place. It filters inherited
`COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`, `COPILOT_SDK_AUTH_TOKEN`,
`COPILOT_PROVIDER_*`, `COPILOT_PROVIDERS_CONFIG`, `GH_HOST` and `COPILOT_GH_HOST`
so they cannot replace the selected profile's login or provider configuration.
Configure custom providers through that home's native `providers.json`. For
Enterprise data residency, pass `--host HOST` to the native profile login command;
see [Copilot login options](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference#copilot-login-options).
Native preferences such as `COPILOT_MODEL` and `COPILOT_AUTO_UPDATE` remain available.

On Linux, the `copilot-session-link` adapter links only the selected session folder
into the destination's `session-state/`. It requires Copilot CLI 1.0.105 and keeps
native login/configuration separate. Production-link SDK checks and a production
wrapper interactive `/rename` preserved the original history without model calls
or real logins. Agent Manager's terminal installation recommendation is also
1.0.105; general CLI/SDK compatibility remains broader.

Both file-history adapters coordinate Manager writers with durable session leases
and a wrapper lock held for the native child's lifetime. Close standalone vendor
sessions before handoff: the native CLIs permit concurrent access, and external
processes do not honor Manager locks. Live Claude PID markers are also checked.

Retained Copilot references require an exact native UUID plus `events.jsonl` and
`workspace.yaml` at their original `session-state/UUID/` paths. Lookup reads no message
or credential contents and rejects missing or symlinked source paths. Discovery reads
bounded native workspace metadata. Destination links are journaled before creation;
existing conversations and untracked links are never replaced. Final terminal command
construction rejects session substitution, remote attachment, home/workspace
redirection and alternate server modes. Native picker launches remain available.

Antigravity supports **one environment per provider registry**, referencing the
current OS user's `~/.gemini/antigravity-cli` and native keyring login. Additional
profile names reference that same environment. A second isolated environment and
duplication are unavailable. A different state root does not isolate native storage.
Rename and alias deletion preserve the home and login.

This is Agent Manager's current support boundary. Preferences and local definitions
cannot redirect that home or enable unqualified retained resume. Caller `HOME`/Windows
`USERPROFILE` overrides cannot point a launch at a different user directory. Manager
preserves the native SSH/keyring environment and does not force file storage, copy tokens or
probe sign-in by submitting `/usage`. Ambient ADC, API credential, custom endpoint
and Cloud project/location overrides are filtered. Interactive sign-in/out remains
provider-owned; see [Antigravity authentication](https://antigravity.google/docs/install/).

The [native resume guide](https://antigravity.google/docs/cli/commands/resume/)
documents `/resume`, `--conversation ID` and workspace-based `--continue`. The latter
can create a fresh conversation when cached history is missing or deleted. Manager
therefore rejects automatic latest-session dispatch and structured retained resumes
until exact identity and history are qualified. Open `agent agy run work`
and select history in the native terminal. Capabilities expose
`retainedSessionResume = false`, `nativeResumeMayCreateSession = true`, and the shared
home reason for disabled cross-profile resume.

The Antigravity SDK remains disabled through Manager because its native profile
login route is unqualified. Package compatibility does not imply route availability;
the SDK is optional, and its installation cannot make a missing CLI usable. Local
inspection verified native 1.4.12 version and the published artifact checksum in a
disposable home. Authenticated
resume and writer handoff were not exercised; no login or model call was made.

The Codex manifest selects `cross_environment_resume = "codex-linux-history"`;
Claude selects `"claude-transcript-path"` and Copilot selects `"copilot-session-link"`.
OpenCode selects `"opencode-session-copy"`; Antigravity uses `"disabled"`. Each enabled selector is restricted to
its built-in provider/home/session strategy; arbitrary manifests cannot declare
universal support. File-history capabilities are false outside Linux and their
installed CLI versions are checked during launch. Metadata-only capability output
includes unchecked version requirements; provider inventory uses its installed
version observation to disable unsupported adapters before launch. See
[terminal version policy](provider-versions.md). Codex capability is false outside
Linux or when `bwrap` is unavailable. Actual Codex continuation also checks optional SDK dependencies, Codex
`>=0.162.1,<0.167.0`, source transcript location, native writer locks, profile identity
and conflicting active runs. Disabled user namespaces fail at native process startup.

Both rollout-backed and paginated histories use their original native SQLite store.
The source index must have completed native backfill; otherwise open Codex in the
original profile and let its indexing finish before retrying. Paginated history must
also retain its native `thread_history_*.sqlite` database. Manager reads discovery and
readiness metadata; native Codex owns history and schema writes.

The cross-profile route uses a fresh native app-server under the destination's real
`CODEX_HOME`, with `CODEX_SQLITE_HOME` and a native `sqlite_home` launch override pointing
to the source store. This override applies only to this continuation process; neither
profile's persistent configuration changes. The source writer-lock directory is bound
over the destination's lock directory inside a private mount namespace, including
coordination for native descendants. The route verifies effective SQLite configuration,
the resumed native ID/path/workspace and the shared writer-lock inode before attaching
a terminal. Terminal history listings use the source index without scanning the
destination's session directory. The destination's normal database remains separate.
Credentials are never staged, copied or converted. Source or target homes
with redirected `sqlite_home`, nested homes, conflicting duplicate IDs and symlinked
writer-lock paths are rejected. Native history stays with the original
session reference. Archiving, closing or replacing that conversation and new/forked
work require a separate launch.

Private launch descriptors and native diagnostics live under
`state/PROVIDER/continuations/`. The temporary socket directory is removed after
the terminal and native server exit. Empty native lock mount points can remain in
profile homes. No native histories are moved or deleted during preparation.
The private diagnostic log also retains Python exceptions from the continuation
process so protocol failures can be distinguished from native startup warnings.

Focused local checks cover launch configuration, identity and failure handling. A
native Codex 0.162.1 and 0.166.0 checks with disposable homes verify paginated reads, source
writer locking and write-back without model calls. The interactive terminal is not
exercised by that check; the development sandbox blocks Unix socket connections.
An unexpected native configuration, identity or lock result fails before a terminal
can start a turn. Other providers retain their same-home behavior.

Resources and launch context are documented in [launch resources](resource-separation.md)
and [launch context](launch-context.md). Carrier launch configuration is owned by
its optional adapter; see [SDK integration contracts](python-sdk.md#managed-execution).

## Optional Undercurrent CLI runtime

Install `aixand-agent-manager[undercurrent]` in the CLI environment and provide the
Undercurrent binaries and daemon to use `runtime = "undercurrent"`. Direct native execution remains the default.
The SDK uses the same optional Undercurrent dependency.
