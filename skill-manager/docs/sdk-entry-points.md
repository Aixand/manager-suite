# Choose a Skill Manager SDK entry point

Use explicit per-skill handles for new integrations. SDK contract version 1 preserves
existing Manager record APIs and native storage. No running suite service is needed.

## Register and capture a selection

The following excerpt assumes a valid `code-review` publisher directory and an installed
Skill Manager environment:

```python
from skill_manager import Manager

manager = Manager(home="/absolute/skill-store")
skill = manager.skill("example-app", "review")
registration = skill.register(
    "/installed/skills/code-review", publisher_revision=12, version="1.4.0",
)
resolved = registration.content
instructions = resolved.read_file("SKILL.md")  # original bytes
status = registration.status
```

Constructing a handle validates identity without reading or creating content.
Registration captures content and matching status together. Surface pending review
and sync errors separately. A stable skill ID can differ from its native name;
exports still use the native name as the destination basename.

`resolve()` reads accepted owned content without refreshing a publisher, fetching
GitHub, or accepting pending changes. A resolved object's reads, export, and release
remain pinned to its captured historical revision. Missing or corrupt historical
content fails rather than falling forward to newer content.

## Select the API for the task

| Task | Entry point |
| --- | --- |
| Register bundled publisher content | `skill.register(path, publisher_revision=...)` |
| Require ingestion failures to raise | `skill.refresh(...)` |
| Inspect accepted bytes | `skill.resolve().read_file(path)` |
| Validate proposed complete content | `skill.validate(changes, revision=...)` |
| Save a user edit | `skill.save(changes, expected_revision=...)` |
| Review an incoming update | `preview_reconciliation()` then guarded `reconcile()` |
| Read history or compare | `history()`, `compare(before, after)` |
| Hold restored content | Guarded `rollback(revision, ...)` |
| Publish or export inspected content | `resolved.release(version)`, `resolved.export(path)` |
| Track project delivery | `manager.project(root)` |
| Fetch a public GitHub update | `skill.check_updates()` with optional GitHub support |

## Edit bytes with an observed guard

```python
from skill_manager import FileContent

current = skill.resolve()
changes = {"references/checks.md": FileContent(b"Check missing tests.\n")}
proposal = skill.validate(changes, revision=current.revision)
saved = skill.save(changes, expected_revision=current.revision)
```

Validation does not save. Saving must use the revision your editor actually read;
a later validation result does not make an older buffer current. `FileContent`
requires bytes and a boolean executable flag. Pass `executable=True` explicitly
when retaining executable status. `None` deletes a file; deletion retains empty
directories. Complete native validation applies before activation.

Serialize `skill.reference.to_dict()` to retain store and publisher/skill identity.
Reopen it with `manager.open_skill(...)`. References do not select a revision and
cannot be opened through another store. Retain the revision separately when needed.
`SkillError` exposes code, message, uncertainty, operation ID, and recovery guidance.

Run the [publisher example](examples/publisher-update.md) before adapting these excerpts.
The [complete SDK reference](python-sdk.md) retains signatures, result types, bounded
inspection, source settings, and reconciliation choices. See [SDK definitions](../src/skill_manager/sdk.py)
and [SDK tests](../tests/test_sdk.py) for implementation evidence.
