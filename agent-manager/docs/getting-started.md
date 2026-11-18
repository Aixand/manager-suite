# Prepare a native terminal agent

Create a native environment, authenticate it, and run the provider terminal. This guide describes the development branch, not a release qualification.

## Hosted installer alternative

On Linux/macOS, the hosted beta installer bundles Python and dependencies, requests confirmation, and separates software from data:

```sh
curl -fsSL https://installer.aixand.org/manager/agent | sh
```

Read [hosted installation](../../docs/release-installation.md#hosted-installer-alternative) for script review, supported targets, Windows PowerShell, and custom stores.

## Install and inspect

Install the CLI with the [suite installation guide](../../docs/INSTALLATION.md). Python consumers install `aixand-agent-manager` into their application's environment. From your project directory, with `agent` on PATH:

```sh
agent --help
agent codex onboarding
agent codex list
```

Onboarding can create a profile. Reuse it if present. Otherwise create one:

```sh
agent codex profile new main
agent codex profile default main
agent codex login main
agent codex doctor --profile main --json
```

Login opens the provider's native flow. Credentials remain provider-owned. Inspect `ready` and `issues`; a zero diagnostic exit means inspection completed, not that sign-in or allowance is verified. See [configuration diagnostics](configuration.md#effective-configuration-and-diagnostics).

## Run and continue

```sh
agent codex run main
agent codex sessions
```

The first command opens a native terminal. Exit through the provider when finished. For continuation, replace `SESSION_REFERENCE` with a returned `ref`:

```sh
agent codex continue SESSION_REFERENCE --profile main
```

A saved conversation is distinct from a running execution. Check [provider policy](provider-versions.md) before moving history between environments. Native terminal work can make account-funded model requests.

## Choose an integration

| Reader task | Interface |
| --- | --- |
| Administer profiles without a daemon | Synchronous `Manager` |
| Retain and recover native terminal execution | Async `Client`, with Undercurrent |
| Expose headless administration tools | [Management MCP](profile-management-mcp.md) |
| Send structured chat messages and handle approvals | [Conversation Manager](../../conversation-manager/README.md) |

CLI execution defaults to direct mode. SDK execution always requires Undercurrent; CLI runtime preferences do not change it.

## Preserve or clean up

Exit native work before changing authentication. Removing an alias preserves its environment and native files. Environment retirement is separate and guarded; read [profiles and environments](profiles-and-environments.md) before forgetting or purging one. Do not remove locks to bypass busy or uncertain execution.
