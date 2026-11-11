# Install a release

Choose the product and version from its release, then download the archive for
your operating system and processor, along with its `-SHA256SUMS.txt` file.
Linux and macOS use `.tar.gz`; Windows uses `.zip`. Linux targets require glibc.
The suite includes all five products; individual archives install one product.
The [candidate notes](beta-release-notes.md) list the qualified native targets and
the minimum OS/libc baselines of the current candidates. Choose an archive whose
`artifacts/release-manifest.json` baseline is compatible with your system.

No Python, uv, Rust, or source checkout is required. The bootstrap needs network
access on first use to obtain its checksum-verified helper interpreter. Product
payloads and their runtime are included in the archive. Provider executables,
authentication, and a running Undercurrent service are separate integrations.

## Linux and macOS

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

## Windows

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
