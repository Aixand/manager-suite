# Validate installer artifacts

Use the suite release tooling to build artifacts and run installation checks.
Run commands from the repository root with the prerequisites in
[installation](INSTALLATION.md) and [development](DEVELOPMENT.md).

```sh
python3 -B scripts/build_installers.py --help
python3 -B scripts/build_installers.py --product agent-manager --output dist/agent-installer
```

Use `--product manager-suite` or omit `--product` for aggregate qualification.
See [independent releases](releases.md) for version tags and CI selection.

The build entry point coordinates product payloads and qualification. Its owning
sources are [build_installers.py](../scripts/build_installers.py) and
[qualify_product_installation.py](../scripts/qualify_product_installation.py).
Inspect their options before selecting a target or reusing an output directory.

Review the generated reports for installed imports, command ownership, artifact
integrity, independent product environments, lifecycle behavior, and data
retention. An upgrade fixture exercises installer mechanics; it does not establish
compatibility with a future application schema.

Keep exact source revisions, platform, dependency versions, commands, and results
with the artifact or review. Old test totals are not evidence for a new checkout.
Native platform jobs must establish their own results; Linux checks do not
qualify Windows or macOS behavior. See [installation](INSTALLATION.md) for current
product selection and release limitations.

External Python dependencies are published artifacts selected by
[dependency pins](../.github/ci/dependencies.json) and the installer lock. The
[artifact fetcher](../scripts/published_dependencies.py) checks wheel
hashes before use; lifecycle verification installs from the resulting offline
wheelhouse. Source integration runs are separate from release qualification.
