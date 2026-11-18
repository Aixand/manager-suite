# Run a local skill integration

Verify owned registration and complete-file delivery without network access or a model.
Use Python 3.11+ and an environment containing Skill Manager and its declared dependencies.
For source setup, follow [development](development.md); for managed CLI installation,
see [suite installation](../../docs/release-installation.md).

## Run from the package directory

```sh
python -B examples/sdk_application.py
python -B examples/project_connection.py
```

The first example creates `code-review/SKILL.md`, adds a user customization, and
observes a pending publisher update. It accepts a valid conflict-free proposal,
exports the complete package, and checks both instruction and reference bytes.
It prints `pending_review_seen: true` and `exported: "exported"` in JSON.

The second example writes text, binary, executable, and empty-directory content.
It verifies preview isolation, applies a pinned connection, updates from the accepted
library, and detaches the unchanged project copy. It prints a completed result.
Both examples use temporary directories and remove them on normal exit.

If either command raises `SkillError`, inspect its `code`, `message`, and recovery
fields. Unsupported atomic directory publication is a filesystem/platform boundary;
there is no overwrite fallback. Interrupted project work requires explicit
[recovery](project-delivery.md#recover-an-interrupted-apply).

## Continue with your own content

Choose [publisher registration](owned-content.md) for a maintained application bundle,
[collection import](projects.md#import-a-local-collection) for a one-time local copy,
or [GitHub sources](github-sources.md) for explicit public repository acquisition.
Use the [SDK](sdk-entry-points.md) to read bytes and retain revisions. Import, validation,
and delivery do not activate skills in a provider.
