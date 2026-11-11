# Manager Suite 0.1.0-beta candidate

This candidate contains five independent managers. Install
one product or the collection containing all five. Python distribution versions
are `0.1.0b0`, equivalent to installer version `0.1.0-beta`.

## Included products

| Product | Commands | Included functionality |
| --- | --- | --- |
| Agent Manager | `agent`, `agent-manager-mcp` | Native environments, authentication profiles, launch context, and management MCP |
| Conversation Manager | `conversation-manager` | Retained conversations, streaming, history, reconnection, approvals, and cancellation |
| MCP Manager | `mcp-manager` | Owned MCP definitions, source imports, preparation, and connector operations |
| Prompt Manager | `prompt-manager` | Owned prompts, composition, publisher updates, and immutable snapshots |
| Skill Manager | `skill-manager` | Owned skills, publisher reconciliation, validation, and export |

Each product has its own Python environment and exposes its own commands. The
collection preserves independently installed products when removed. Normal
uninstall preserves runtime data.

The default Agent profile includes management MCP and the Undercurrent SDK.
Conversation includes Agent integration and native Codex transport. Provider executables, authentication,
and a running Undercurrent service are configured separately. Optional provider
SDKs, Carrier, Runner, and Connect are outside this candidate's default profiles.

## Candidate platforms

Release builds qualify Linux x64/ARM64 with glibc, macOS ARM64, and Windows x64
on native CI runners. Prompt, Skill, and MCP Manager also have Intel Mac installers.
Agent, Conversation, and the collection defer Intel Mac installers for this beta.
Agent's default management MCP dependencies require a current cryptography release,
whose upstream Intel Mac support has ended. Each archive declares its required operating
system baseline, which the installer checks before installation. The attached
qualification reports identify the exact verified archives. Windows ARM64 and
Linux musl are outside the current installer targets.

## Installation

Download the selected product archive and its `-SHA256SUMS.txt` file. Verify the
archive checksum, extract it, and follow the bundled `INSTALL.md`. No Python,
uv, Rust, or source checkout is required. First use of the bootstrap requires
network access to obtain its checksum-verified helper interpreter; the product
payload and its Python runtime are included in the archive.

Attached qualification reports identify the exact archive and manifest hashes.
Later rebuilt bytes need their own reports. Only archives attached to a published release are release artifacts.
Applications embedding the tools must qualify their own installation and ownership
behavior separately.

## Known limits

- Live Codex checks passed on Linux for inference, reconnection, same-profile
  continuation, cancellation, permission denial, and cleanup. Other account and
  platform combinations require separate live qualification.
- OpenCode V2 passed real-executable tests with localhost model fixtures on all
  three native platforms. External inference and review remain unqualified after
  the attempted free route returned a usage-limit response.
- Installer upgrade fixtures cover software transitions and data retention. A
  future release must test data created by the previous released application
  before advertising an application upgrade path.
- Stateful rollback remains blocked until a migration-aware adapter exists. Stop
  product processes and back up their data before upgrades.

## Feedback

Report problems through the project's issue tracker with the product version,
platform, command, and a minimal reproduction. Include relevant diagnostic output
with credentials and private model content removed. External pull requests are
currently closed because maintainer review capacity is limited.
