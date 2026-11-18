# Publish source-owned Manager Suite documentation

Edit the owning source guide, then import an exact committed revision into the
project site. Do not edit generated site snapshots. General explains shared setup
and composition; each product owns its architecture, lifecycles, contracts and
examples. Keep explanations concise and use accessible Mermaid where it clarifies
relationships or recovery.

## Select the owner

| Area | Publication manifest |
| --- | --- |
| General | `docs/publication.json` |
| Agent Manager | `agent-manager/docs/publication.json` |
| Conversation Manager | `conversation-manager/docs/publication.json` |
| MCP Manager | `mcp-manager/docs/publication.json` |
| Prompt Manager | `prompt-manager/docs/publication.json` |
| Skill Manager | `skill-manager/docs/publication.json` |

Keep product docs and examples inside their product route namespace. Preserve
useful old URLs through reviewed redirects when pages move. Normative references,
qualification evidence, policies and maintainer runbooks need not all be published.

## Verify and import

From the Manager Suite checkout:

```sh
python3 -B scripts/checks/check-docs.py --root .
```

Run relevant provider-free examples in explicit temporary stores. Render diagrams
in both themes and inspect the actual site renderer; parsing alone is insufficient.
Describe authenticated provider checks separately from fixtures. Save screenshots
only when UI behavior benefits from them and record capture scope.

From the sibling project-site checkout, repeat for the relevant manifest:

```sh
npm run docs:sync -- --source ../manager-suites --repository https://github.com/Aixand/manager-suite --manifest docs/publication.json --revision SOURCE_SHA --output .
npm run build
```

Replace `SOURCE_SHA` with a full commit SHA. Product imports retain separate
provenance under `src/content-imports/manager-suite/`. General uses
`src/content-imports/manager-suite.json`. Declared assets copy runnable examples
from the same commit and record their hashes. Do not maintain a second code copy
inside prose; link to the executable source or generate a checked excerpt.

For review before committing, use `--working-tree --output /tmp/manager-doc-preview`.
That preview has no committed provenance and must not be deployed. Push the
source revision before public deployment so GitHub source links resolve.

## Connected integration examples

The general manifest publishes ten complete workflows under `connected/` and the
connected setup guide. Nine use actual SDKs and private services with bounded,
explicitly labeled local fixtures; the live worker/reviewer uses an existing
authenticated profile and runs only when selected explicitly.

Check inline source excerpts without starting services:

```sh
python docs/examples/connected/check_snippets.py
```

Check the nine repeatable applications with a matching runtime:

```sh
python docs/examples/connected/verify.py --runtime-bin /absolute/path/to/bin
```

Record actual terminal runs with VHS using
`docs/examples/connected/recordings/record.py --runtime-bin /absolute/path/to/bin`.
The runner needs VHS, ttyd, ffmpeg with libopenh264, ffprobe and Chromium. It
retains each effective tape, executed dependency hashes, media details and observed
cleanup in its capture JSON. Publish only the declared videos, posters, tapes and
capture records; credentials and runtime stores stay in disposable work directories.
Recording assets import into `public/demos/manager-suite/` from the same pinned
source revision. Do not replace command output with preprinted result text.
