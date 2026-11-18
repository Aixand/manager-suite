# Create and render your first prompt

This guide uses local commands with no provider or model. Choose an explicit store
to keep tutorial data separate from your working library.

## Install the CLI

The hosted beta installer is an alternative to release archives on Linux/macOS:

```sh
curl -fsSL https://installer.aixand.org/manager/prompt | sh
```

It bundles Python and dependencies, asks for confirmation, and separates software
from manager data. Review the script and supported targets in
[hosted installation](../../docs/release-installation.md#hosted-installer-alternative),
including Windows PowerShell and custom stores.

For Python consumers, install into your application's environment. From this
package's source directory, an editable installation is:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Build tooling uses setuptools; installed runtime operations use only the standard
library. On Windows activate `.venv\Scripts\Activate.ps1`.
For pinned contributor tooling, follow [development](development.md).

## Install and create

From a shell with the CLI installed:

```sh
export PROMPT_MANAGER_HOME="$PWD/.prompt-manager"
prompt-manager managed create review --content 'Review the supplied work carefully.'
prompt-manager managed render local review review
```

PowerShell uses `$env:PROMPT_MANAGER_HOME = "$PWD/.prompt-manager"`.
The create command returns JSON. Managed rendering also returns JSON containing
`output` and provenance. `local` is the publisher, and both package and recipe IDs
are `review`. Creation runs synchronously and rejects an existing ID.

For a complete Python workflow with assertions and automatic temporary-store
cleanup, run [the owned example](examples/owned-workflow.md).

## Default storage location

| Priority | Selection |
| --- | --- |
| 1 | Explicit SDK `home` or CLI `--home` |
| 2 | `PROMPT_MANAGER_HOME` |
| 3 | `$AIXAND_MANAGER_ROOT/prompt` |
| 4 | `${XDG_DATA_HOME:-~/.local/share}/aixand/manager/prompt` |

Unset, empty, or relative `XDG_DATA_HOME` falls back to `~/.local/share`.
Explicit/product overrides are used directly. For example,
`AIXAND_MANAGER_ROOT=/custom/managers` selects `/custom/managers/prompt`.
Path resolution creates no directories and does not relocate or adopt old stores;
supply an existing store's original path explicitly. Other products' configuration
and native provider homes remain separately owned.

Draft discovery uses explicit `packs`, `PROMPT_MANAGER_PACKS`, or `./packs`.
Use `Manager(home=path, packs=[])` to disable working-directory draft discovery.

## Read command results and failures

Global CLI options precede the subcommand. Inspection and mutation commands return
JSON. Direct `render` returns raw text unless `--json` is supplied; managed renders
return JSON. Operational errors emit JSON on stderr and exit 1; usage errors exit 2.
The library raises `PromptError` with a `code` at its documented public boundaries.
See [SDK compatibility](python-sdk.md#public-contracts-and-compatibility).

JSON mutation inputs accept inline JSON or `@FILE`. Direct `render --inputs` takes
a file path; managed JSON inputs accept inline JSON or `@FILE`.
Use `prompt-manager --help` and subcommand `--help` for argument details.
