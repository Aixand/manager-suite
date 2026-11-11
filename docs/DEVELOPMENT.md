# Develop a package independently

Use a package-local environment to change one manager or shared library. Python
3.11+ and uv 0.12.7 are the contributor prerequisites. The root environment remains
available for integration work; local checks do not require it.

| Package | Local development guide |
| --- | --- |
| Agent Manager | [Guide](../agent-manager/docs/development.md) |
| Conversation Manager | [Guide](../conversation-manager/docs/development.md) |
| MCP Manager | [Guide](../mcp-manager/docs/development.md) |
| Prompt Manager | [Guide](../prompt-manager/docs/development.md) |
| Skill Manager | [Guide](../skill-manager/docs/development.md) |
| Filesystem | `../../aixand-libraries/filesystem/docs/development.md` |
| Local runtime | `../../aixand-libraries/local-runtime/docs/development.md` |
| GitHub source | `../../aixand-libraries/github-source/docs/development.md` |

From the selected package directory:

```sh
./dev.sh doctor
./dev.sh develop
./dev.sh check
./dev.sh test
./dev.sh build
```

PowerShell uses `.\dev.ps1` with the same arguments. `check` checks dependencies
and static errors; `test` runs the package tests. `build` creates a wheel and source
archive from a clean snapshot. Each command uses the package's own configuration
and environment. A new lock is written only by the explicit `lock` operation.
Registry dependencies are pinned with hashes; local first-party inputs become
versioned entries so development locks contain no repository or machine paths.

`--local` opts into editable sources declared by that package. For an extracted
source archive, use `develop --wheelhouse /path/to/dependency-wheels` instead.
The package helper does not consult sibling source paths unless `--local` is set.
Installed products still own their dependencies and need no shared manager service.

## Template adoption

The helper follows the installation-template revision-2 local command meanings.
Each package vendors its own runnable helper so it can travel with the source
archive. Its settings live in `development.toml`; tools and tested third-party
constraints are local, and normal project metadata remains in `pyproject.toml`.
The [canonical helper](../scripts/development/python_dev.py) and its
[adoption record](../scripts/development/template-revision.json) maintain the
copies together. This is a scoped adapter, not a claim of adopting every template
capability or independently qualifying each operating system.

Documentation follows standard 0.1.0: keep the README as the entry point and the
local development guide as the owner of development instructions. API and product
guides live under each package's `docs/` index. Participation, license,
security and repository policies remain shared at the repository root; package
source archives retain their own Apache-2.0 LICENSE. No publishing workflow or
remote repository setting is changed by local setup.

## Suite integration and release artifacts

Use the root helper for coordinated integration and managed release artifacts.
The existing installation-template release adapter retains artifact integrity,
command ownership, install/update/uninstall, and collection packaging. Package
entry points forward supported release operations to their product or component
when the full checkout is available. A detached checkout can still develop, test,
and build wheels/source archives without the suite helper. Libraries install
through their consumer package manager and have no global command lifecycle.

See [installation](INSTALLATION.md) for managed products and release profiles.
Native Windows/macOS qualification remains separate from Linux checks.


## Check documentation

Run from the repository root after moving or editing a guide:

```sh
python3 scripts/checks/check-docs.py --root .
```

The checker covers suite documentation, manager guides, requirements, and the
reference chat README. It checks local links, anchors, Markdown structure, and
template placeholders. It does not execute examples or verify external URLs.

## Published dependency inputs

Normal CI and root development use published libraries and Carrier, with the
published `aixand-undercurrent-sdk` wheel from PyPI. Versions, artifact
URLs, and SHA-256 values live in
[dependencies.json](../.github/ci/dependencies.json); root and installer locks
include the SDK wheel hash. Artifact preparation verifies existing cache entries too.

```sh
uv lock --check
# Optional: fetch and verify an explicit wheel cache.
python3 scripts/published_dependencies.py aixand-undercurrent-sdk
```

`./dev.sh develop` installs only the suite's manager sources as editables.
Shared libraries, Carrier, and the Undercurrent SDK come from the registry.
No external MCP source checkout is required.

CI builds dependency wheels for unpublished managers from this checkout, so an
Agent change is tested by Conversation Manager against the changed Agent code.
External libraries remain the published wheels. Carrier's optional `agent-manager`
extra in its current published beta still names the old Agent/SDK distributions; CI uses
Carrier's base package and does not enable that extra.

For deliberate cross-repository source integration, use a package's
`develop --local` or enable `source-integration` on a manual product CI run.
Release qualification always uses published external dependencies.

## Participation and private reports

The repository currently accepts issues rather than pull requests because of
limited maintainer capacity. See the [participation policy](../CONTRIBUTING.md).
Use [support](../SUPPORT.md) for public reports and private ownership concerns,
and [security](../SECURITY.md) for vulnerabilities.

The root development group includes the published Carrier API and MCP test
dependencies. Release product profiles retain their own dependency selections.

For affected-package checks and product-specific release tags, see
[selective CI and releases](releases.md).
