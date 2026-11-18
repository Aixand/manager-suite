# Integrate owned prompts and logical selections

Consumers choose when to ingest defaults and when to execute a model. Prompt
Manager owns content decisions and rendering; it does not start the consumer.

## Assign responsibilities

| Owner | Responsibility |
| --- | --- |
| Consumer application | Deliver source paths, order publisher revisions, show review status, call models |
| Prompt Manager | Capture, validate, preserve user edits, reconcile, render accepted snapshots |
| External draft author | Keep registered files available and coordinate edits |
| Native package backend | Validate/persist its format and implement its maintenance contracts |

For bundled defaults, call `package.register()` explicitly and use its effective
content/status pair. Do not compare publisher digests or overwrite customization
in the caller. Show pending review while keeping accepted content active.
See [publisher workflow](owned-prompts.md) and [Python SDK](python-sdk.md).

## External drafts and logical selections

This excerpt assumes `local/review` already exists in `manager`:

```python
manager.register_application("workspace", prompts={
    "review": {"kind": "managed", "publisher_id": "local",
               "package_id": "review", "prompt": "review"},
}, expected_revision=None)
result = manager.render_application("workspace", "review")
```

Logical descriptors select managed content (optionally pinned by state revision),
immutable releases, or registered drafts. They have no provider or launcher
assumptions. Use `application_descriptor(id)` for normalized read-only lookup.
Descriptor-list failures retain string errors and structured diagnostics.

Mapping updates use observed revisions. Draft edits, content release publication,
and mapping changes remain separate guarded operations. Missing mappings fail by
default. Explicit call-scoped packaged fallbacks are available; invalid configured
targets never silently fall back. See [target definitions](../src/prompt_manager/applications/targets.py),
[resolution](../src/prompt_manager/applications/resolution.py), and
[mapping tests](../tests/sdk/test_mappings.py).

## Share lifecycle policy across formats

Use [PackageLifecycle](package-lifecycle.md) for a trusted native package backend.
The backend owns format validation, capture, digests, locks, and persistence.
Prompt Manager supplies ordering, accepted-state policy, history, reconciliation,
and organization. This does not make an external backend store a prompt store:
`Manager.check_store()`, backup, and restore validate prompt-format records only.

## Standalone boundary and compatibility

Remote repository discovery/synchronization, background imports, provider setup,
application startup, and tool-specific migration policy belong outside this core.
Inert source provenance does not initiate network checks. Legacy
`application(..., bundled_pack=...)` construction refreshes defaults; prefer explicit
package handles for new code. Existing descriptor registrations are not silently
migrated to owned content.

See [architecture boundaries](architecture.md) and
[standalone tests](../tests/integration/test_standalone.py) for isolated execution.
