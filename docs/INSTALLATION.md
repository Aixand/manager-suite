# Product installation

Manager Suite uses revision 2 of the sibling installation-template. Each CLI
product installs into its own Python environment. The aggregate selects all five;
it preserves individually installed products when the aggregate is removed.

| Product ID | Public commands | Bundled Python API dependencies |
| --- | --- | --- |
| manager-suite/mcp-manager | mcp-manager | aixand-lib-filesystem, aixand-lib-github-source |
| manager-suite/prompt-manager | prompt-manager | None |
| manager-suite/skill-manager | skill-manager | Prompt Manager, aixand-lib-filesystem, PyYAML |
| manager-suite/conversation-manager | conversation-manager | aixand-lib-filesystem, aixand-lib-local-runtime, Agent Manager and native Codex transport dependencies |
| manager-suite/agent-manager | agent, agent-manager-mcp | aixand-lib-filesystem, aixand-lib-local-runtime, Undercurrent SDK, management MCP dependencies |

These are internal library copies in the selected product's environment.
Installing Agent Manager alone publishes only `agent` and `agent-manager-mcp`.
It does not register Prompt Manager or the other managers as installed products.
The `aixand-lib-filesystem`, `aixand-lib-local-runtime`, and
`aixand-lib-github-source` utilities are published Python libraries without CLIs.

The default Agent profile enables `mcp,undercurrent`; Conversation enables
`agents,native-codex`. These profiles
are recorded in `installation.toml` and locked in `installer/uv.lock`. Python
package base/extras retain their original optionality.
Provider executables, a running Undercurrent service, Runner, Carrier and Connect
remain explicit integrations. Claude and Antigravity SDK extras are not included
in these initial release profiles.

For downloaded archives, follow [Install a release](release-installation.md).
The same guide is included as `INSTALL.md` inside every release archive.

## Build from source

The five manager sources are included in this repository. No external MCP source
checkout is required.

Build tools are uv 0.12.7 and Python 3.12.14; Windows also needs Rust 1.98.0 for
the native command shim. The consuming machine needs none of these build tools.
The shared libraries and `aixand-undercurrent-sdk` come from PyPI as published
wheels. Installer assembly checks their locked hashes before use.
[Dependency pins](../.github/ci/dependencies.json) and `installer/uv.lock` bind the
exact bytes. No library, Carrier, or Undercurrent source checkout is required.

```sh
uv python install 3.12.14
python3 -B scripts/build_installers.py --output dist/installers
```

Use a fresh output directory. This builds the aggregate, all five individual
artifacts; verifies installed APIs; exercises
offline lifecycle and a Python-free bootstrap; then exports checksum-bound
handoffs. It does not publish or deploy.

For one product:

```sh
./prompt-manager/dev.sh package --output dist/prompt-only
./dev.sh verify --artifact dist/prompt-only --install
```

PowerShell equivalents use `dev.ps1`; the build orchestration script works on
all selected native targets. The targets are GNU/Linux x64 and ARM64, macOS Intel
and Apple Silicon, and Windows x64. A declared target is not a qualification
claim: see [validation status](installer-validation.md).

## Install all or install one

```sh
./dev.sh install --artifact dist/installers/aggregate \
  --prefix "$HOME/.local/share/aixand/products" --scope shared
export PATH="$HOME/.local/share/aixand/products/bin:$PATH"

# Alternatively, consume just one artifact in an independent store:
./agent-manager/dev.sh install --artifact dist/installers/products/agent-manager \
  --prefix "$HOME/.local/share/aixand/agent-tools" --scope shared
```

Artifact installation is offline and includes CPython 3.12.14 and all selected
wheels. The aggregate has five environments and one shared, checksummed runtime
within its installation store. A different store owns a separate runtime copy.
No shell profile or user PATH is changed automatically by these developer calls.

Generated `bootstrap/bootstrap.sh` and `bootstrap/bootstrap.ps1` can run without
system Python. On first use they download and verify the exact helper interpreter,
then consume the artifact. This first-stage download requires network access or
an already populated, verified cache. Fully offline installation can use the
artifact consumer with an available Python 3.11+.

```sh
sh dist/installers/bootstrap/bootstrap.sh install \
  --artifact dist/installers/aggregate --prefix "$HOME/.local/share/aixand/products"
```

The future hosted routes are `manager-suite/install.sh` and
`manager-suite/<product>/install.sh`, with PowerShell counterparts. These routes
are a publishing contract; this change does not make them live.

## Private application bundles and data

An embedding application should use an owned prefix,
`--scope private --owner example-app`,
and launch its tools by absolute path or through the installed consumer. Keep
that bin directory out of global PATH. Shared products live outside this prefix,
so removing the application directory leaves them usable.

Scope controls software ownership. It does not redirect data or provider homes.
Applications must explicitly pass separate, absolute state/config paths:

| Product | Explicit data configuration |
| --- | --- |
| Prompt | `--home`, `PROMPT_MANAGER_HOME`, or `Manager(home=...)` |
| Skill | `--home`, `SKILL_MANAGER_HOME`, or `Manager(home=...)` |
| MCP | `--home`, `MCP_MANAGER_HOME`, or `Manager(home=...)` |
| Conversation | `--home`, `CONVERSATION_MANAGER_HOME`, or `Client(home=...)` |
| Agent | `AGENT_MANAGER_HOME`/explicit manager root and provider config |

Configure the Undercurrent service home and bridge path explicitly when enabling
that runtime integration. Bundling the SDK does not start a daemon or select a
user's service. Provider authentication is never imported by the installer.

## Updates and removal

`inspect` and `plan` are read-only. Installation emits JSONL progress with
`--events jsonl`; retry the same artifact and options with the same `--request-id`
to resume. Request IDs are bound to their input, and versions are immutable.
Use `reinstall` to repair the same release or `upgrade` for changed release bytes.
An installed aggregate pins its exact child releases; coordinated updates must
update the aggregate or remove that selection first.

```sh
./dev.sh inspect --prefix "$HOME/.local/share/aixand/products"
./dev.sh uninstall --prefix "$HOME/.local/share/aixand/products" \
  --product manager-suite --stopped
```

Stop owned processes before removal and back up quiesced data before upgrades.
Stateful rollback is deliberately blocked until a migration-aware adapter exists.
Uninstall removes owned software and commands, preserving product data.
An explicitly installed manager survives aggregate removal; remove it separately
with `--product manager-suite/<name>`. Inactive runtimes require explicit pruning.

## Installer ownership

`installation.toml` is the suite's installation manifest. Root and package
release commands use the same product installer. Supply the artifact and product
selection shown above for install, repair, upgrade, and removal.

The installer operates only on its own receipts and does not adopt unrelated
source installations. Development uses the root lock and supports
`UV_PROJECT_ENVIRONMENT` for a disposable environment. Product packaging uses
the separate installer lock.

Intel Mac installers are available for Prompt, Skill, and MCP Manager. Agent,
Conversation, and the collection defer Intel Mac installers for this beta because
Agent's default management MCP dependencies no longer provide current Intel Mac
cryptography wheels. Other platforms keep their current dependency versions.
