# Beta support and limits

The beta includes Agent, Conversation, MCP, Prompt, and Skill Manager. Each has an independent installer; the suite selects all five.
Read the [candidate notes](beta-release-notes.md) for the native candidate set
and its exact OS/libc baselines. Use [Install a release](release-installation.md) for archive installation and
[Product installation](INSTALLATION.md) for included dependencies and profiles.

## Platform coverage

Installer support and live provider support are separate. A successful archive
check covers extraction, bootstrap delivery, installed command execution,
inspection, and removal. Suite qualification also checks shared/private ownership,
standalone product reuse, API isolation, upgrade fixtures, and retained data.

| Target | Beta qualification |
| --- | --- |
| Linux x64 with glibc | Native collection and five manager archives; see attached qualification reports |
| macOS ARM64 | Native collection and five manager archives; see attached qualification reports |
| Windows x64 | Native collection and five manager archives; see attached qualification reports |
| Linux ARM64 with glibc | Earlier snapshot passed under emulation; native hardware remains pending |
| macOS Intel | Targeted; native qualification remains pending |

The candidate packet includes reports for the collection and five individual
manager archives. Applications embedding them need their own integration and
installation checks.

Keep exact artifact hashes and qualification reports with release artifacts.
A passed source snapshot run does not qualify later rebuilt bytes. Emulation
does not establish native hardware behavior. The installer rejects systems older
than the OS/libc baseline declared in the selected artifact. Windows ARM64 and Linux musl are
outside the current installer targets.

POSIX bootstraps require `sh`, `curl`, `tar`, and a SHA256 utility (`sha256sum`
or `shasum`). Windows bootstraps require PowerShell 5.1+ and the system `tar.exe`.
These prerequisites are separate from the bundled product runtime.

## Provider integrations

Provider executables, authentication, and model access are separate from the
manager installers. The Undercurrent SDK is bundled where selected; a running
Undercurrent service is an explicit integration.

| Integration | Coverage and limits |
| --- | --- |
| Agent Manager with Codex and Undercurrent | Live Linux checks cover inference, reconnection, same-profile saved-session continuation, retained context, and shutdown |
| Conversation Manager with native Codex | Live Linux checks cover streaming/history, reconnection, cancellation, and permission denial; Windows/macOS have transport fixture coverage |
| Conversation Manager with OpenCode V2 | Real executable with localhost model fixtures qualified on Linux x64, Windows x64, and macOS ARM64; external model inference/review remain unqualified after a free-route usage limit |
| Claude, Copilot, and Antigravity SDKs | Optional integrations; not qualified by these installer checks |
| Carrier, Runner, and Connect | Optional integrations; outside the default installer qualification |

Read the [provider version policy](provider-versions.md) for supported versions
and startup requirements. A successful model turn on one account, route, or
platform does not qualify every combination. Live checks are opt-in and can
consume account allowance; [run provider qualification](../conversation-manager/docs/live-qualification.md)
for the configuration being supported.

## Updates and data

Normal uninstall preserves product data. Removing the suite preserves products
that were independently installed. Stop product processes and back up their data
before upgrades. Stateful rollback remains blocked until a migration-aware
adapter is available.

Upgrade assembly fixtures prove installer transitions and data retention. They
do not establish compatibility with a future application's schema. Before a
subsequent release advertises an upgrade path, add fixtures created by the
previously released application version. Exercise populated data, reopening after
upgrade, failure recovery, and the documented rollback policy. Changed formats
also need explicit migration coverage. Record supported upgrade paths; do not
infer them from package version numbers.

## Reporting a problem

Use [Support](../SUPPORT.md) and file an issue with the product version, platform,
command, and a minimal reproduction. Remove credentials and private model output
from reports. External pull requests are currently closed because of maintainer
capacity; see [Contributing](../CONTRIBUTING.md).
