# Dependency and notice inventory

All five manager distributions ship their own unchanged Apache-2.0 LICENSE and
declare Apache-2.0 in package metadata. No project NOTICE is introduced without an
actual attribution requirement. Existing source/resource attribution is retained.

| Material | Source | Use and notice handling |
| --- | --- | --- |
| Manager source and resources | This repository's five component directories | Packaged by each component's setuptools rules; Apache LICENSE included |
| Developer helper | Aixand installation-template, helper revision 2 | Vendored source in `scripts/dev`; workspace product configuration |
| aixand-lib-local-runtime 0.1.0b0 | PyPI wheel | Apache LICENSE retained; local IPC, WebSockets, process identity and shutdown signals |
| aixand-lib-filesystem 0.1.0b0 | PyPI wheel | File locks, permissions and pinned Windows paths; Apache LICENSE retained |
| aixand-lib-github-source 0.1.0b0 | PyPI wheel | GitHub source acquisition; Apache LICENSE retained |
| aixand-carrier-mcp 0.1.0b0 | PyPI wheel | Optional provider and development dependency; Apache LICENSE and NOTICE retained |
| aixand-undercurrent-sdk 0.1.0b0 | PyPI wheel | Universal Python wheel with a locked hash; Apache LICENSE retained; matching native runtime installed separately |
| Community policies and baseline workflows | Aixand open-source-template | Project policies customized; Apache LICENSE retained |
| PyYAML 6.0.3 | PyPI `PyYAML` | Skill parsing; upstream MIT notice retained inside its wheel |
| websockets 16.0 | PyPI `websockets` | Native server transport; upstream BSD notice retained inside its wheel |
| jsonschema 4.25.1 | PyPI `jsonschema` | Native RPC validation; upstream MIT notice retained inside its wheel |
| claude-agent-sdk 0.2.168 | PyPI `claude-agent-sdk` | Optional official SDK and bundled Claude runtime; upstream files and notices retained in its wheel |
| google-antigravity 0.1.22 | PyPI `google-antigravity` | Optional official SDK and bundled Antigravity runtime; upstream files and notices retained in its wheel |
| github-copilot-sdk 1.0.26 | PyPI `github-copilot-sdk`, upstream tag `v1.0.26` | Optional MIT SDK; its wheel omits LICENSE, so the unchanged official license is included in Conversation Manager and as a hashed inventory supplement; compatible Copilot CLI installed separately, runtime download disabled |
| OpenCode CLI test fixture | [Reviewed native archive pins](../.github/ci/opencode-fixtures.json) | SHA512-verified test executable; localhost model fixtures only; excluded from manager payloads |
| Their locked dependencies | Root `uv.lock` | Exact downloaded wheels and bundled notices recorded for each suite package |
| setuptools 84.1.0, wheel 0.46.2, pip 26.2, Ruff 0.15.7 | PyPI, root development lock | Build/check tools; not shipped as suite runtime wheels |
| windows-curses | PyPI, Windows marker in root runtime group | Conditional runtime dependency; Linux suite builds do not ship Windows wheels |

Product payload assembly creates dependency inventories from the actual
wheel METADATA and archive members. It records exact versions, declared license
expressions/classifiers, notice paths, and SHA-256 hashes. Packaging fails if a
runtime wheel has no distributed license notice or reviewed, exact-wheel-bound
supplement. The Copilot SDK 1.0.26 supplement is pinned to the wheel SHA-256 and
the upstream tag's LICENSE bytes; it does not modify the wheel. The outer manifest hashes the
complete inventory and wheelhouse. Dependency notices stay inside the original
wheels and are installed by the package installer. This inventory supports review;
it does not infer ownership or approve unknown license terms from metadata alone.

Development and CI consume published external packages. Product packaging uses
`installer/uv.lock` and the component dependencies in `installation.toml`.
[Published artifact pins](../.github/ci/dependencies.json) identify registry wheels
including `aixand-undercurrent-sdk`. CI can stage verified wheels in ignored
`build/published-wheels/`; the SDK also resolves directly from the registry.
Release manifests bind the actual installed wheel bytes and notices.
