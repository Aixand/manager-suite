# Install a release

Use the hosted installer below, or choose the product and version from its release
and download the archive for your operating system and processor, along with its
`-SHA256SUMS.txt` file.
Linux and macOS use `.tar.gz`; Windows uses `.zip`. Linux targets require glibc.
Individual published archives install one product. A collection artifact selects
all five; its hosted route remains unavailable until a qualified collection is published.
The [candidate notes](beta-release-notes.md) list the qualified native targets and
the minimum OS/libc baselines of the current candidates. Choose an archive whose
`artifacts/release-manifest.json` baseline is compatible with your system.

No Python, uv, Rust, or source checkout is required. The bootstrap needs network
access on first use to obtain its checksum-verified helper interpreter. Product
payloads and their runtime are included in the archive. Provider executables,
authentication, and a running Undercurrent service are separate integrations.

## Hosted installer alternative

The five independent beta releases can also be installed from
[the Aixand Installer](https://installer.aixand.org). On Linux or macOS, choose
one manager:

```sh
curl -fsSL https://installer.aixand.org/manager/agent | sh
curl -fsSL https://installer.aixand.org/manager/conversation | sh
curl -fsSL https://installer.aixand.org/manager/mcp | sh
curl -fsSL https://installer.aixand.org/manager/prompt | sh
curl -fsSL https://installer.aixand.org/manager/skill | sh
```

Each command installs only the selected manager's public commands. Agent exposes
`agent` and `agent-manager-mcp`; the others expose their matching `*-manager`
command. Bundled API dependencies remain inside that manager's environment.
Run more than one command to install several independent managers. The all-five
`/manager` collection route is reserved and currently returns HTTP 503 because
no collection release is registered.

The hosted beta is `0.1.0-beta`. All five support Linux x64/ARM64 with glibc,
macOS ARM64, and Windows x64. Prompt, Skill, and MCP also support Intel Macs.
Agent and Conversation have no Intel Mac archive for this beta. The published
archives and their qualification reports determine the supported OS baselines.

The script downloads a fixed, checksum-verified release, safely extracts it,
asks for installation confirmation, and delegates installation to the archive's
bootstrap. No system Python, uv, Rust, or checkout is required. The first run
needs network access for the product archive and the bootstrap's pinned helper
interpreter. Installation does not start services, install providers, import
credentials, or move existing manager data. Python API consumers still install
the relevant package into their own application's environment.

On Windows, download and run the PowerShell script. This example installs Agent;
replace `agent` in the URL with `conversation`, `mcp`, `prompt`, or `skill` for
another manager:

```powershell
Invoke-WebRequest -UseBasicParsing -Uri 'https://installer.aixand.org/manager/agent/install.ps1' -OutFile install-manager.ps1
Get-Content .\install-manager.ps1
.\install-manager.ps1
```

Local PowerShell script execution policy applies. Open a new terminal after
shared command registration. Existing command precedence is kept by default.

To review the shell script and installation plan first:

```sh
curl -fsSL https://installer.aixand.org/manager/agent -o install-manager.sh
cat install-manager.sh
sh install-manager.sh --plan
sh install-manager.sh --install
```

PowerShell equivalents are `.\install-manager.ps1 -Mode Plan` and
`.\install-manager.ps1 -Mode Install`. Explicit install mode is suitable for
automation after review; these releases declare no required license-acceptance
digest. `--download-only` or `-Mode DownloadOnly` retains the verified archive
without extracting or invoking it. Plans retain a verified bundle too; remove
the printed temporary directory after use. Keep a verified extracted bundle
for the bootstrap lifecycle commands below.

Hosted installs use a shared, user-owned software store:

| Platform | Default software store |
| --- | --- |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/aixand/products` |
| macOS | `~/Library/Application Support/Aixand/products` |
| Windows | `%LOCALAPPDATA%\Aixand\products` |

An absolute `AIXAND_SHARED_PRODUCT_PREFIX` overrides the shared default;
`--prefix /absolute/path` or `-Prefix 'C:\absolute\path'` takes precedence.
On Linux/macOS the wrapper creates links in `~/.local/bin`, preserving unrelated
existing commands; add that directory to PATH if needed. Windows delegates user
PATH registration to the bootstrap. `--no-commands` or `-NoCommands` skips command
exposure. Private application installs require an explicit prefix, private scope,
and application owner; see [private application bundles](INSTALLATION.md#private-application-bundles-and-data).
Software location does not change manager data paths or `AIXAND_MANAGER_ROOT`.

The default channel is beta. Append `?version=0.1.0-beta` to select that exact
registered release, or use `?channel=beta`. Stable remains disabled and returns
HTTP 503. Versions are immutable and previous registered versions stay available.
The service uses a bundled catalog; it does not discover latest GitHub releases
at request time. Hosted scripts provide plan, install, and verified download;
use the archive bootstrap for the explicit updates and removal described below.

## Linux and macOS archives

Run these commands in the download directory, replacing `ARCHIVE` and `SUMS` with
the downloaded filenames. The checksum file also lists the attached release
reports; `--ignore-missing` verifies the files you downloaded.

```sh
# Linux:
sha256sum --check --ignore-missing SUMS
# macOS: compare this output with the archive entry in SUMS:
shasum -a 256 ARCHIVE

tar -xzf ARCHIVE
cd aixand-PRODUCT-VERSION-TARGET
product_store="$HOME/.local/share/aixand/products"
if [ "$(uname -s)" = Darwin ]; then
  product_store="$HOME/Library/Application Support/Aixand/products"
fi
sh bootstrap/bootstrap.sh install --artifact ./artifacts --prefix "$product_store"
sh bootstrap/bootstrap.sh inspect --prefix "$product_store"
```

The default software store is `~/.local/share/aixand/products` on Linux and
`~/Library/Application Support/Aixand/products` on macOS. Add its `bin` directory
to PATH in your shell configuration, then open a new terminal. For this terminal:

```sh
export PATH="$product_store/bin:$PATH"
```

Run the installed command with `--help`, for example `agent --help` or
`prompt-manager --help`. `product-descriptor.json` lists every included command.

## Windows archives

In PowerShell, compare the hash with the archive entry in the downloaded checksum
file, then extract and enter the archive directory:

```powershell
Get-FileHash .\ARCHIVE.zip -Algorithm SHA256
Expand-Archive .\ARCHIVE.zip -DestinationPath .\release
Set-Location .\release\aixand-PRODUCT-VERSION-TARGET
$productStore = "$env:LOCALAPPDATA\Aixand\products"
powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap\bootstrap.ps1 install --artifact .\artifacts --prefix $productStore
powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap\bootstrap.ps1 inspect --prefix $productStore
$env:PATH = "$env:LOCALAPPDATA\Aixand\products\bin;$env:PATH"
```

The default store is `%LOCALAPPDATA%\Aixand\products`. Add its `bin` directory to
your user PATH for future terminals. Installation does not edit shell profiles.

## Updates, repair, and removal

Keep the extracted archive for lifecycle commands. To update, download and verify
the new release, enter its extracted directory, and run its bootstrap with
`upgrade --artifact ./artifacts --prefix "$product_store"`. Use
`reinstall --artifact ./artifacts --prefix "$product_store"` to repair
the same release. Stop product processes and back up their data before updating.
Stateful rollback is blocked until a migration-aware adapter is available.

To inspect or remove an installation on Linux/macOS:

```sh
sh bootstrap/bootstrap.sh inspect --prefix "$product_store"
sh bootstrap/bootstrap.sh uninstall --prefix "$product_store" --product manager-suite/PRODUCT --stopped
# Remove the suite selection instead:
sh bootstrap/bootstrap.sh uninstall --prefix "$product_store" --product manager-suite --stopped
```

On Windows, replace `sh bootstrap/bootstrap.sh` with
`powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap\bootstrap.ps1`
and `$product_store` with `$productStore`.
Replace `PRODUCT` with the product name, such as
`agent-manager`. `--stopped` confirms you have stopped processes using the software.
Uninstall preserves runtime data. Removing the suite preserves independently
installed products. Products retained as dependencies cannot be removed alone.

For a custom software store, pass the same absolute `--prefix` to every lifecycle
command. Software location does not redirect product data. The installer refuses
to overwrite unrelated installations; inspect reported conflicts before retrying.
