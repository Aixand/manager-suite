# Provider version policy

`provider-policy/manifest.toml` defines exact CLI installation defaults, tested
versions, supported ranges, known incompatible releases, backend requirements,
SDK pins and native artifact checksums. This is the authority for Agent Manager,
Conversation Manager and Suite onboarding.

Automatic installation never selects `latest`. Compatible independently installed
releases remain usable; a release need not be in the tested list to be within a
supported range. Native and SDK startup still negotiate protocols and verify
required capabilities. A range is not a promise that every future feature works.
Unknown versions, prereleases and known incompatible releases are rejected for
chat integration. Terminal usability is reported separately.

Current defaults, ranges, tested versions, and exclusions are listed under
`providers.<name>.cli` in the [canonical manifest](../provider-policy/manifest.toml).
SDK pins are under `providers.<name>.sdk` in the same file.

Codex remains pre-1.0, so its minor boundary is explicit. ACP adapters negotiate
protocol version 1 separately; Codex ACP and Claude ACP can supply their own CLI
runtime. Antigravity SDK does not require the interactive CLI. Claude SDK and
Copilot SDK require a compatible configured CLI as well as their exact SDK pin.

Claude 2.1.310 fails the managed approval callback and steering integration tests
with SDK 0.2.168. The same five Claude SDK tests pass with 2.1.302 against localhost
model fixtures. The known-incompatible entry prevents chat from silently accepting
that release while leaving independent terminal use available.

## Maintenance behavior

- Install keeps an existing compatible release. Update selects the tested release
  and keeps a newer compatible version.
- Repair reinstalls the current compatible version. **Restore tested version**
  explicitly chooses the manifest default, including a downgrade. Neither removes
  accounts, profiles or project data.
- Incompatible newer releases and unknown versions require explicit restoration.
  Unknown installation owners, custom locations and method changes require manual
  resolution. Existing Homebrew installs are detected but remain user-managed.
- Retained jobs capture the target version, method, policy revision and observed
  installation. SDK jobs also capture their requirement hash. A changed plan fails
  before work begins; retry creates a new plan from the current inventory.
- Version mismatch or installation failure rolls back native executables and npm
  package/command replacements. Antigravity checks its pinned SHA512 and executable
  version before publishing the replacement.

Antigravity has its own background updater. The install pin controls initial
installation, while actual CLI observations determine compatibility afterward.
Claude sessions launched by Aixand disable its updater for that launch without
changing the user's independent CLI settings.

## Updating a release

1. Qualify the CLI or SDK using the native adapter tests and required protocol and
   capability checks. Record tested versions and deliberate support boundaries.
2. Change the canonical manifest. For SDK changes, update the corresponding root
   `sdk-*` dependency group and Conversation Manager extra, then run `uv lock`.
   Add official versioned asset URLs and SHA512 checksums when native artifacts
   are used. Do not replace a versioned URL with a floating bootstrapper.
3. Run `python scripts/provider_policy.py` to generate identical policy modules,
   manifests and complete hashed SDK requirements in both independent packages.
4. Run `python scripts/provider_policy.py --check` and the provider policy,
   installer and native backend tests before packaging.

The generator's check catches policy, package metadata, lock-input and generated
asset drift. Suite and Manager Suite build paths run it before wheel assembly.
Wheels include all policy assets, so installed products do not read a source
checkout or need the other Manager package to load the policy. SDK installers use
the packaged requirements with `--require-hashes`, including transitive packages.
