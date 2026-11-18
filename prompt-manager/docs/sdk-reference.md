# Choose a Python SDK operation

Use `Manager` for the store and `Package` for explicit publisher/package handles.
Constructing a handle does not read content or refresh a publisher.
Python 3.11+ is required; no provider or service is needed.

## Bind and use a handle

This excerpt assumes the publisher pack exists and declares recipe `review`:

```python
from prompt_manager import Manager

manager = Manager(home="/absolute/prompt-store", packs=[])
package = manager.package("publisher", "default")
registered = package.register("/absolute/bundled-pack", publisher_revision=1,
                              prompt_id="review")
print(registered.content, registered.status["outcome"])
```

`register()` returns effective rendered content with its captured status.
Strict `refresh()` ingests without rendering. The
[publisher example](examples/publisher-review.md) exercises both ownership and
failure retention. See [owned prompts](owned-prompts.md) for update decisions.

## Select the contract

| Task | Operations | Full reference |
| --- | --- | --- |
| Read accepted content | `status`, `resolve`, `render` | [Explicit handle](python-sdk.md) |
| Check or edit content | `validate`, `save`, `compare` | [Candidate/guard contracts](python-sdk.md) |
| Review and retain history | `preview_reconciliation`, `reconcile`, `history`, `restore` | [Managed lifecycle](MANAGED.md) |
| Deliver accepted content | `release`, `export` | [Rendering](rendering.md), [imports](imports.md) |
| Organize metadata | `rename`, `archive`, `unarchive`, `duplicate`, `set_tags` | [Organization](library-management.md) |
| Find accepted metadata | `search_managed`, `refresh_metadata` | [Search](search.md) |
| Inspect or recover the store | `check_store`, `backup_store`, `restore_store` | [Reliability](storage-reliability.md), [backup](backup-restore.md) |

Use observed state revisions for guarded mutations. Content digests, state tokens,
ordered publisher revisions, and display versions serve different purposes.
A serialized `PackageReference` is bound to its original store; opening it against
another home conflicts. Obtain a new handle after full-store restore.

## Handle results and failures

`SDK_CONTRACT_VERSION` is 1. Public `TypedDict` types describe JSON-compatible
results; they do not provide runtime wrappers. Allow additional result fields.
`RegistrationResult` exposes `.content` and `.status`.
Package operations normalize filesystem failures to `PromptError(code="io")`;
domain error codes survive. Other Manager methods retain their documented boundary
behavior. Do not assume every exception means a mutation did not happen.

The legacy `application(..., bundled_pack=...)` constructor refreshes defaults.
Use `application_descriptor(id)` for a logical descriptor lookup and explicit
package handles for new ownership integrations.

The retained [Python SDK reference](python-sdk.md) owns complete signatures,
result shapes, fallback rules, and compatibility. Verify changes against
[public types](../src/prompt_manager/interfaces/contracts.py),
[handle implementation](../src/prompt_manager/interfaces/sdk.py), and
[SDK tests](../tests/sdk/test_sdk.py).
