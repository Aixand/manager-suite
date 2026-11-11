# Terminal versions and continuation

## Feature-specific qualification

`agent PROVIDER doctor --json` reports installed and recommended versions alongside
the qualified model/budget interface versions. A generally compatible CLI can have
an unsupported account reader. Updating installation recommendations must not
silently widen feature pins.

OpenCode 2.2.5 model discovery and the integration metadata/budget bridge were
checked against the installed native CLI in a disposable home with credential
environment variables cleared. The returned catalog and bridge payloads passed the
production normalizers. Those interfaces now accept 2.2.5 as well as 2.0.18.
This check did not exercise authenticated account allowance or widen continuation
support beyond its separate qualified policy.

Equivalent unsigned-in probes did not qualify Claude 2.1.316 account usage
(`unsupported` response) or Antigravity 1.4.12 usage (`timeout`); Antigravity model
discovery returned `unavailable`. Their previous exact feature pins remain intact.
Copilot's recommended 1.0.105 budget interface still requires native qualification;
the installed 1.0.104 executable cannot establish that newer interface's behavior.
Doctor reports these gaps explicitly without claiming an account is signed out or
exhausted. Existing fixture tests remain protocol fixtures, not live-account proof.

Reproduce bounded metadata probes without changing real profiles or policy:

```sh
python tests/manual/qualify_features.py --provider opencode --features models integrations
python tests/manual/qualify_features.py --provider claude --features budget
```

The harness returns JSON, uses a temporary home, clears credential environment
variables, and does not modify pins automatically. An authentication failure or an
empty response does not establish authenticated quota support.

## Installation and continuation policy

Agent Manager's terminal recommendations track the latest approved provider
releases. CLI installation, SDK/backend compatibility and history-adapter
qualification remain separate.
Exact adapter pins exclude unverified vendor/build variants as well as other
release numbers.

| Provider | Recommended CLI | Cross-environment continuation | Verification |
| --- | --- | --- | --- |
| Codex | 0.166.0 | `>=0.162.1,<0.167.0`; Linux, `bwrap` and enabled user namespaces | Native history/write-back/locks checked with disposable homes on 0.162.1 and 0.166.0; full interactive check pending |
| OpenCode | 2.2.5 | Exactly 2.0.18; native transcript copy | 2.2.5 native import overwrote an existing session in the disposable safety probe, so cross-environment continuation remains on 2.0.18 |
| Claude Code | 2.1.316 | Exactly 2.1.316; Linux transcript-path adapter | Offline native resume/write-back checked |
| GitHub Copilot CLI | 1.0.105 | Exactly 1.0.105; Linux session-link adapter | Offline native resume/write-back checked |
| Antigravity | 1.4.12 | One shared native environment; aliases share it | Structured retained continuation unavailable |

The OpenCode adapter is implemented for Linux, macOS and Windows, but remains
pinned to 2.0.18 because the 2.2.5 overwrite behavior violates its no-clobber
contract. Codex cross-profile continuation uses the built-in native RPC client and base WebSocket dependency. No
authenticated multi-account model turns have been exercised by these local checks.
See [file-session continuation](file-session-continuation.md) and
[OpenCode copies](opencode-continuation.md) for history and writer limitations.

## Installation behavior

Inspect the release catalog shipped with this Agent Manager:

```sh
agent versions opencode
agent versions opencode --json
agent install codex --version latest --reinstall --dry-run
agent install opencode --version 2.0.18 --yes
agent install codex --version 0.162.1 --yes
agent uninstall codex --dry-run
```

| Selector | Meaning |
| --- | --- |
| Omitted / `recommended` | Latest approved release used for fresh managed installations |
| `latest` | Highest approved release with a supported recipe for the chosen host and installation method |
| An exact version, optionally prefixed with `v` | That approved release, subject to compatibility and method/platform restrictions |

`latest` resolves from local policy into an exact native release, never a moving
vendor installer channel. Blocked versions, unsupported platforms and missing
verified artifacts are excluded. Updating Agent Manager's catalog changes what
the selector resolves to. Listing versions starts no provider process and performs
no network lookup; tool prerequisites and download availability are checked during
installation planning/execution.

The current fresh-install recommendation equals the latest approved release for all
five providers. Continuation compatibility and verification remain independent and
appear alongside each version. Version/platform compatibility does not prove runtime
prerequisites or native verification; continuation retains its own launch checks.

Fresh installs and setup instructions use the latest recommendations above. Ordinary
CLI compatibility is broader: for example, OpenCode 2.2.5 works with profiles but is
outside the copy adapter's version limit. A compatible existing installation is
kept by installation, and an equal or newer compatible native version is kept by update.
Repair also preserves its current compatible version unless restoring the
recommendation is explicitly selected. Continuation never installs or downgrades
a CLI.

A fresh explicitly selected version must be in `install_versions` in the terminal
policy. A broader-range-compatible version already on the machine can be kept, and
repair of a native installation without a selector can reinstall that same compatible version. Its presence
does not add it to the approved catalog or change `latest`. Unknown, blocked and
out-of-range new selections are refused before downloading or installing.

Managed CLI install, update and repair support only official native installers or
verified standalone downloads: curl/shell on Linux and macOS, and PowerShell/native
downloads on Windows. npm, Homebrew and other package-manager methods are rejected
by the CLI, SDK planner, queued maintenance and installer execution. Existing
external CLIs remain discoverable and usable with profiles; changing their software
or removing it requires their original package manager. Managed changes require
manual migration to a native installation. Terminal-command setup does
not migrate packages or provision a Node runtime. SDK Python dependencies retain
their separate installation mechanism.

`agent uninstall PROVIDER` uses the same provider-software ownership checks as the
management SDK. It removes a verified native, npm or Homebrew installation and its owned command
exports while preserving profiles, native homes, authentication, settings and
history. npm removal verifies the package entry and owning prefix; Homebrew removal
verifies the package location. Active managed runs block removal. Bundled CLIs,
custom executable locations and unverifiable ownership are reported
for manual removal instead of being deleted. A subsequent fresh `agent install`
reenables a provider that was disabled by uninstall.

To explicitly change an existing native installation:

```sh
agent install opencode --reinstall --version 2.0.18 --yes
agent install copilot --reinstall --version 1.0.105 --yes
agent update opencode --version latest --dry-run
```

`install --version ...` preserves an existing installation unless `--reinstall`
is supplied when its version would change. Default update keeps an equal or newer
compatible release. Explicit update selectors can choose an older approved release;
the preview identifies a downgrade and shows both installed and target versions.
Dry-run performs no download or installation and publishes no success receipt.

CLI installation honors global provider `--config` and state `--home` context when
checking executable overrides. Software destinations remain normal user-wide
installation locations, independent of profile homes.

Copilot installation verification and inventory use the recipe's
`--binary-version` probe. Its ordinary `--version` can select a newer cached CLI,
which made a successful 1.0.104-to-1.0.103 downgrade appear to install the wrong
version in the first real container run. The binary probe identifies the installed
payload; native launcher cache selection and automatic updates remain Copilot
behavior. This check neither deletes that cache nor pins later native launches.
See the [upstream loader report](https://github.com/github/copilot-cli/issues/4728).

Exact versions use a versioned official installer or verified download. Custom
executable locations can require manual maintenance. SDK versions, their dependency
locks and native provider installation policy are unchanged by terminal
recommendations.

## Catalog defaults and compatibility

Catalog defaults are reviewed versions, not a claim about the latest upstream
release. Inspect the provider catalog before changing an installation pin.

OpenCode 2.2.5 passed an empty-home metadata inspection but changed two reviewed
contracts: native import overwrote an existing session and the synthetic integration
endpoint no longer matched. It remains the normal installation default, while
cross-environment continuation and reviewed integrations stay pinned to 2.0.18.
Antigravity's six platform manifests all reported 1.4.12; their artifact URLs and
SHA-512 digests are pinned in the shared policy. Its authenticated model and usage
adapters were not widened by this release review.

Linux and macOS share the shell installer flow but download different OS/architecture
binaries. Windows uses `.exe` files, different user installation locations, PowerShell
or archive extraction, registry-backed user PATH registration and different file
locking behavior. Therefore passing Linux checks does not qualify the other hosts.
Copilot currently has no qualified native Windows installation recipe in Agent
Manager; setup/catalog output reports it unavailable instead of falling back to npm
or WinGet. An already installed Copilot executable remains usable with profiles.

## Shared installation plans

CLI install/update, SDK planning and retained maintenance use
`providers/installation/plans.py`. A plan captures the selector, exact target,
action, requested and resolved method, host, destination, executable override and existing installation
fingerprint, plus general/terminal policy and recipe revisions. It reports
continuation version/platform compatibility and remaining prerequisites.

Execution revalidates the plan and checks it again under the user-wide installation
lock before invoking an installer. Changed executable configuration, installation,
host, recipe or policy invalidates it. Method execution receives only the resolved
exact version. Existing ownership, PATH-conflict, migration, rollback and executable
verification checks remain in place.

Successful receipts include `requestedSelector`, `resolvedVersion`, `requestedVersion`
and policy/recipe revisions. Retained maintenance accepts `versionTarget` as
`recommended`, `latest` or an approved exact version, with `reinstall` required for
a version-changing install. Store the resolved result as `installationPlan`;
queued execution must not silently resolve a different release later. Retained
maintenance preserves its active-profile-run guard. Local installation receipts are
stored under `~/.local/share/aixand/manager/agent/state/installations/`. Provider conversation
SDK installation is outside Agent Manager.

## Real installation checks in containers

From the Agent Manager checkout, run:

```sh
python3 tests/manual/installation_containers.py
```

This is a manual host check, independent of CI. It requires Python 3.11+, working
rootless Podman, internet access, and the sibling `../aixand-libraries/filesystem` and
`../aixand-libraries/local-runtime` libraries used by the development installer. Use
`--runtime-source PATH` and `--filesystem-source PATH` for other locations.
An unavailable container runtime fails the preflight without running vendor tests.

The runner builds an image from copies of the package sources, then starts a fresh
container for each provider: native installer for Codex, OpenCode, Claude and
Copilot; verified artifact installation for Antigravity. The image includes no
Node.js, npm or Homebrew.
Each container has one CLI release at a time, an unprivileged user, and separate
home, configuration, cache and state directories. Host directories and
runtime sockets are not mounted, and host proxy environment variables are not
forwarded. No login, profile creation or model-turn command is invoked.

Checks exercise dry-run, exact releases, recommended/latest selectors, preservation
of compatible installations, explicit reinstall, updates/downgrades where the
catalog offers multiple releases, repair through the shared planner, rejected
versions, executable discovery and installation receipts. Versions are read from
the bundled catalog. A failed download or installer fails the case; it is never
reported as a successful installation check. Containers are removed after their
logs and JSON findings are collected; the uniquely tagged test image is also
removed. Podman may retain downloaded base images and build cache in its normal
storage. Existing provider installations and configuration are not modified.

For a smaller initial check, or to inspect the context without using Podman:

```sh
python3 tests/manual/installation_containers.py --provider codex
python3 tests/manual/installation_containers.py --prepare-only
```

The runner prints the results directory, with `results.json`, build/preflight logs
and a log/findings file for each attempted container. `--output PATH` selects a
new directory; `--timeout SECONDS` bounds each container. `--prepare-only` retains
the copied build context and reports `prepared`, without claiming vendor checks
passed. Linux container results do not qualify macOS or Windows paths,
or authenticated provider/profile/session behavior.

The container isolation flags follow the
[Podman run reference](https://docs.podman.io/en/latest/markdown/podman-run.1.html).

Retain per-provider versions, results, and cleanup findings in the generated
report. To rerun only Copilot:

```sh
python3 tests/manual/installation_containers.py --provider copilot
```

## Public capability output

Metadata-only profile inspection runs no CLI. Its `crossEnvironmentResume` value
describes adapter/platform availability, and
`crossEnvironmentResumeCompatibility.status` is `unchecked`. The compatibility
object includes `supportedRange`, `recommendedVersion`, `platforms`, `prerequisites`,
`verification`, `verificationNotes` and the terminal `policyRevision`. Its
`compatible` value is null until a version is observed. Antigravity and providers
without a versioned adapter return null for this object.

Provider inventory already observes the installed CLI version. It reuses that
observation for both provider capabilities and each listed profile's capabilities.
The version status becomes `compatible`, `incompatible` or `missing`. An unsupported
or unknown version disables `crossEnvironmentResume` and supplies an actionable
`crossEnvironmentResumeReason`. Ordinary terminal readiness is unaffected by an
adapter-only version mismatch. Platform and execution prerequisites still apply
even when the version matches. A compatible version does not imply that the full
native route has been verified; read the separate verification fields.

Launch rechecks the executable before modifying history routes or importing a
copy. Queued CLI installation plans retain both general and terminal policy
revisions; a changed policy invalidates the plan before execution.

## Policy ownership

`agent_manager/providers/terminal-policy.toml` is the bundled source for terminal
recommendations, approved `install_versions` and continuation requirements. Runtime version guards, managed
installation, setup and inventory read the same policy. Local provider manifests
select supported mechanisms; they cannot widen these version limits.
`provider-policy.toml` remains the generated shared source for general CLI and SDK
compatibility. Keep the two policies distinct when qualifying a new native release.

Installation recipes stay in `providers/installation/install_catalog.toml`.
`releases.py` combines recipes with policy and derives continuation support from
its existing policy. Platform-native installers and verified artifact downloads
reuse their existing execution paths. Approving a release normally changes policy
data; a new installation mechanism requires implementation code.
