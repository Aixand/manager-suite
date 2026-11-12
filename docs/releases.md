# Check and release one manager

Use selective CI for everyday changes and product tags for independent releases.
A package check never publishes software. A release builds native installers only
for the selected product; downstream consumer checks do not release those consumers.

## Run checks

Push a branch to run repository checks and affected package checks on Linux with
Python 3.11. Shared build or dependency inputs select all products. Documentation-only
changes run repository checks without creating a product matrix.

| Changed input | Additional consumer checks |
| --- | --- |
| Agent Manager | Conversation Manager |
| Prompt Manager | Skill Manager |
| Conversation, Skill, or MCP Manager | None |
| Provider policy | Agent Manager and Conversation Manager |
| Shared installer, workflow, tooling, or lock inputs | All five managers |

Open **Actions → Product validation → Run workflow** for manual checks. Select one
product to check only that package, `all` for every package, or `affected` for
conservative change detection. A manual `affected` run has no push comparison and
checks all packages. Enable `full` to use Python 3.11 and 3.14.

A missing or unavailable comparison commit also selects all products. Deletions
count as changes. Prompt-pack Markdown is a product input, unlike prose guides.
The dependency map lives in [ci_plan.py](../scripts/ci_plan.py) and
[manager_profiles.py](../scripts/manager_profiles.py).

Checks prepare each package through its own development helper, run its tests,
build fresh wheel/source archives, and consume the installed wheel outside the
source directory. Optional live provider tests are separate from this workflow.
At most two product jobs run concurrently; a newer run replaces superseded
checks for the same branch and manual selection.

Normal checks consume pinned published dependencies from
[dependencies.json](../.github/ci/dependencies.json). The shared libraries and
Carrier and `aixand-undercurrent-sdk` use PyPI wheels. Cached bytes, SHA-256 pins,
and wheel identities are verified.
Current in-suite dependency wheels are built from this checkout for consumer tests.

For explicit source integration, enable `source-integration` on a manual check.
That option also uses `AIXAND_LIBRARIES_REPOSITORY`/`AIXAND_LIBRARIES_REF`,
and `AIXAND_UNDERCURRENT_REF`.
It is never enabled by a release workflow. Changes in sibling repositories do not
trigger this repository's path detector; run the relevant checks manually.

## Prepare independent versions

For the product being released, update these values together:

1. Its static `project.version` in `pyproject.toml`.
2. Its exported `__version__` in `_version.py` or `__init__.py`.
3. Its product `version` in [installation.toml](../installation.toml).
4. Its exact dependency-group requirement in [installer/pyproject.toml](../installer/pyproject.toml),
   then regenerate and review `installer/uv.lock`.
5. Its local development locks and affected consumer locks when their dependency
   requirements change.

Stable versions match directly. Installer prereleases use `0.2.0-beta.3`, while
Python metadata uses `0.2.0b3`; the initial `0.1.0-beta` maps to `0.1.0b0`.
The [release validator](../scripts/ci_release.py) rejects mismatches. Each product
can have a different version; the suite group has its own version.

Conversation and Skill installers bundle manager API dependencies. An Agent release
does not alter already-published Conversation installers. If a bundled dependency
changes in a rebuilt installer, give that changed product payload a new version.
Do not replace an existing product release with different bytes.

## Pin release inputs

All five manager sources come from this repository. No external product source
checkout or MCP repository pin is required. Published dependency versions, wheel
URLs, and hashes are recorded in
[dependencies.json](../.github/ci/dependencies.json) and the dependency locks.
Review changed dependency pins together with the selected product versions.

The Python SDK wheel does not install Undercurrent's native runtime. For runtime
integration, provide the daemon and bridge matching its `runtime_release` in the
artifact pins. Installing SDK dependencies never starts an existing user service.

## Build a candidate or publish a product

Open **Actions → Product and suite releases → Run workflow** and choose a product
to build a candidate. This runs qualification and uploads workflow artifacts without
creating a GitHub Release. It uses the committed release pins.

To publish, commit the reviewed release inputs and push the matching product tag:

```sh
# Example: after Agent's package and installer versions have become 0.2.1.
git tag aixand-agent-manager/v0.2.1
git push origin aixand-agent-manager/v0.2.1
```

The accepted tag prefixes are the five `aixand-<product>/v` distribution names.
A tag's version must match that product's installer version. Tags such as `v0.2.1`
do not trigger a product release.

The release workflow:

1. Tests the selected package and its downstream consumers on Python 3.11 and 3.14.
2. Builds and qualifies only the selected installer family on Linux x64/ARM64,
   macOS ARM64, and Windows x64, with at most two native jobs at once. Prompt,
   Skill, and MCP Manager also qualify Intel Mac installers. Agent, Conversation,
   and the collection defer Intel Mac installers for this beta.
3. Checks installed imports, public commands, offline lifecycle behavior, corruption
   rejection, removal guards, and data retention. Conversation releases also run
   native transport fixtures.
4. Exports each qualified product with its bootstrap, `INSTALL.md`, candidate
   release notes, verification reports, source provenance, and SHA-256 checksums.
5. Requires repository tooling, documentation, policy, and security checks to pass.
6. Downloads all selected native outputs, verifies checksums and immutable product
   identities, creates a draft GitHub Release, attaches all assets, and publishes it.

A failed target prevents publication. Existing GitHub Releases are never overwritten.
A failed upload can leave a draft: inspect or remove that draft before retrying.
Release runs are not cancelled by subsequent pushes. Per-product releases are not
marked as the repository's global latest release; stable suite releases are.

The `installer.yml` workflow publishes each product to PyPI and attaches its
wheel and source distribution to its GitHub release alongside native installers.
The collection has only GitHub assets. Hosted installer catalog entries are separate.
The qualified `0.1.0-beta` individual releases are available through `installer.aixand.org/manager/<name>`;
see [hosted installation](release-installation.md#hosted-installer-alternative).
Future releases require verified registration and hosted qualification before
moving their catalog channel. A product tag or PyPI upload alone does not change
the hosted installer. The all-five collection route remains disabled.

Configure each PyPI trusted publisher with owner `Aixand`, repository
`manager-suite`, workflow `installer.yml`, and environment `pypi-<product>`
(for example, `pypi-prompt-manager` for `aixand-prompt-manager`). Each product
uses a distinct environment. MCP Manager uses `pypi-manager-suite` to match its
configured publisher. The collection does not publish a PyPI distribution.
No PyPI API token is needed. Initial Python versions
are `0.1.0b0`, corresponding to installer tags ending in `/v0.1.0-beta`.

All qualification gates pass before PyPI publication. PyPI publishes before the
GitHub release; these services cannot publish atomically. Retry failed jobs after
inspecting the failure. Existing PyPI files must have identical names and SHA-256
hashes; retries upload only missing files. Never move a published release tag. Repository release immutability is a separate GitHub setting;
see [GitHub's release guidance](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases).

## Release the suite

Bump `[groups.manager-suite].version` and tag, for example,
`aixand-manager-suite/v0.4.0`. Product versions remain independent. The suite release
runs all package checks and qualifies aggregate/individual ownership and payload
identity on every supported target.

This first implementation rebuilds the suite from its pinned source inputs. It
does not download and compose previously published product artifacts. That reuse
requires a separate assembly step. Review changed product payloads and bump their
versions before tagging the suite.

## Exercise the tooling locally

Run from the repository root, using a new output directory each time:

```sh
python3 scripts/ci_plan.py --product agent-manager --full
python3 scripts/ci_release.py validate --product agent-manager
python3 scripts/build_installers.py --product agent-manager --output dist/agent-candidate
```

The last command performs a real native build and installed lifecycle checks.
Omitting `--product` builds the complete suite. See
[installation](INSTALLATION.md) for prerequisites and end-user operations and
[installer validation](installer-validation.md) for qualification scope.

## Immutable product versions

Every native release attaches a checksum-bound `-payload-identities.json` record
with each bundled product's version, target, and engine release hash. Before
publication, the release workflow compares all current records against identity
records from earlier published releases, including suite releases. A changed
payload under the same product/version/target fails publication; bump that
product's Python, runtime, installer, and lock versions before rebuilding.
Targets have separate identities. The first release establishes the baseline;
releases predating identity records cannot be compared automatically.

Final archives are extracted and exercised through their bundled bootstrap with
Python removed from PATH before upload. Attached archive qualification reports
cover install, command help, inspection, and removal. Standalone lifecycle tests
also exercise a successful version-changing assembly fixture and data retention.
That fixture validates installer transitions, not a future application's schema
migrations; real migration behavior needs tests when an application changes it.

## Native OpenCode fixture

Conversation and suite installer checks prepare the reviewed OpenCode version
with [the fixture helper](../scripts/prepare_opencode_fixture.py). The helper
verifies the committed SHA512 archive integrity, extracts only the native
executable, and checks its version. The native/frontend tests use a localhost
model server and need no account credentials. An unavailable or changed binary
fails preparation instead of silently skipping those tests.

When changing the OpenCode default in the provider policy, review and update
[the fixture archive pins](../.github/ci/opencode-fixtures.json) for every target.
These binaries are test tools; they are separate from manager installer payloads.

Archive release notes must identify the selected installer version in their first
heading. Put independent product notes at `docs/release-notes/<product>/<version>.md`
or collection-wide notes at `docs/release-notes/<version>.md`. The initial beta
uses [the candidate notes](beta-release-notes.md). Packaging rejects stale notes
when a later version has no matching document.
