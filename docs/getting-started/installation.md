# Install Manager Suite

Install the independent manager commands or prepare an application environment for their public Python APIs.

## Install a published beta

Install the managers you need independently. No system Python, uv, Rust, or source checkout is required. On Linux or macOS:

```sh
curl -fsSL https://installer.aixand.org/manager/agent | sh
curl -fsSL https://installer.aixand.org/manager/conversation | sh
curl -fsSL https://installer.aixand.org/manager/mcp | sh
curl -fsSL https://installer.aixand.org/manager/prompt | sh
curl -fsSL https://installer.aixand.org/manager/skill | sh
```

Each command installs one manager. The all-five `/manager` collection route is reserved and unavailable. The beta supports Linux x64/ARM64 with glibc, macOS Apple Silicon, and Windows x64. Prompt, Skill, and MCP also support Intel Macs; Agent and Conversation do not yet provide Intel Mac archives. Check the release qualification reports for minimum OS versions.

On Windows, choose the manager in the URL, review the script, and run it:

```powershell
Invoke-WebRequest -UseBasicParsing -Uri 'https://installer.aixand.org/manager/agent/install.ps1' -OutFile install-manager.ps1
Get-Content .\install-manager.ps1
.\install-manager.ps1
```

The bootstrap asks for confirmation and obtains its checksum-verified helper interpreter over the network. Installation preserves existing manager data. Provider executables, authentication, and runtime services are separate. Add `~/.local/bin` to PATH on Linux/macOS if necessary; open a new terminal on Windows after command registration.

See the [release installation guide](../release-installation.md) for script review, pinned versions, archive downloads, custom stores, updates, and removal. Python API consumers install their dependencies into their own application environment; hosted commands do not expose a shared Python environment.

## Set up the complete suite

The site's Python quickstart and examples use the root development environment. Install Python 3.11–3.14 and uv 0.12.7, then run from the Manager Suite checkout:

```sh
./dev.sh doctor
./dev.sh develop
source .venv/bin/activate
agent --help
prompt-manager --help
skill-manager --help
mcp-manager --help
conversation-manager --help
```

Normal development consumes published dependencies selected by the checked-in locks. Package-local `./dev.sh develop --local` explicitly selects sibling sources. See [package development](../DEVELOPMENT.md) for prerequisites and PowerShell commands. Continue with the [offline quickstart](quickstart.md).

## Keep existing data separate

Use a dedicated home for tutorial work. `AIXAND_MANAGER_ROOT` relocates the suite's default directories; explicit component paths take precedence. Existing stores are not moved or adopted automatically. Follow [storage and boundaries](../concepts.md) and [troubleshooting](../troubleshooting.md) before reconnecting an existing store.
