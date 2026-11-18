# Choose a provider release safely

Check ordinary CLI compatibility separately from native history continuation. A compatible installed version does not qualify every feature.

## Read the two policies

| Source | Owns | Does not establish |
| --- | --- | --- |
| [General policy](../agent_manager/providers/provider-policy.toml) | CLI compatibility, blocked versions | Native history handoff safety |
| [Terminal policy](../agent_manager/providers/terminal-policy.toml) | Install allowlist, history-adapter pins, prerequisites and evidence | Authenticated model-turn qualification |
| [Installation catalog](../agent_manager/providers/installation/install_catalog.toml) | Mechanisms and recipes | Permission to widen version limits |

Local manifests cannot widen terminal version limits. Plans retain policy revisions; a changed policy invalidates a queued installation plan before execution.

## Decide whether to continue elsewhere

| Provider | Captured native handoff limit | Evidence limit |
| --- | --- | --- |
| Codex | Linux, 0.162.1 through versions below 0.167.0; bubblewrap and user namespaces | Partial; full interactive terminal check pending |
| Claude | Linux, exactly 2.1.316 | Offline resume/write-back; no authenticated model-turn check |
| Copilot | Linux, exactly 1.0.105 | Offline resume/write-back; no authenticated model-turn check |
| OpenCode | Exactly 2.0.18; transcript copy | Synthetic checks; native verification pending |
| Antigravity | Structured retained resume unqualified | Native history picker can create a session |

The development source recommends OpenCode 2.2.5 for installation, but its import safety probe overwrote existing history. Do not infer copy support from that installation recommendation.

Metadata-only inspection reports compatibility as `unchecked`. Provider inventory can reuse its installed-version observation; launch rechecks before preparing history. Even matching versions still require the declared platform and prerequisites.

## Verify your installation

From your project directory:

```sh
agent codex doctor --profile main --json
```

Inspect feature-specific compatibility and issues, not only the command exit status. Use the [offline policy example](examples/policy-snapshot.md) to inspect bundled data without starting a provider. Follow the full [version reference](provider-versions.md) for installation dry-runs, update policy, container qualification, and evidence retention. Linux checks do not qualify Windows or macOS.

Owning checks: [terminal policy tests](../tests/test_terminal_policy.py) and [version tests](../tests/test_provider_versions.py).
