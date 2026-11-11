# Managed metadata search and tags

Prompt Manager searches personal and publisher-managed packages using their local
state records. It does not open prompt bodies, render recipes, fetch publisher
sources, or write a search index. Tags use guarded metadata updates.

## Tags

```python
package = manager.package("publisher", "main")
observed = package.status()
tagged = package.set_tags(["Review", " Python "], expected_revision=observed["revision"])
# tagged["tags"] == ["python", "review"]
```

`set_tags()` replaces the complete tag list. Pass an empty sequence to clear it.
Inputs contain at most 32 strings. Tags are trimmed, Unicode-casefolded, sorted,
and deduplicated; each normalized tag must contain 1–64 characters. Tags can contain
spaces and are not templates or executable instructions. Invalid UTF-8 JSON text
is rejected through the existing JSON contract.

A successful change creates a managed history revision without changing content,
publisher ordering, tracking, or customization status. The observed revision is
required; stale writes fail even when the requested tags match the current list.
Repeating an equivalent normalized list with the current guard is a no-op.

Tags survive publisher updates, reconciliation, archival, content restoration, and
full-store backup/restore. Duplication copies the tags from the selected source
state by value; editing the copy's tags does not change the original. Display labels
retain their existing separate duplication behavior.

## Search

```python
report = manager.search_managed(
    "parser review", publisher_id="publisher", tags=["python"],
    archived=None, customized=True, reconciliation_required=True,
)
for item in report["items"]:
    print(item["publisher_id"], item["package_id"], item["label"], item["description"])
```

Text matching is Unicode-casefolded substring matching. Whitespace separates query
terms, and every term must match at least one metadata field. Terms can match
across publisher/package IDs, display label, accepted pack name, accepted pack
description, and tags. Empty/whitespace queries match all packages allowed by the
filters. There is no stemming, fuzzy ranking, phrase syntax, or body-content search.
Recipe-level descriptions are not part of this pack-level search.

| Filter | Behavior |
| --- | --- |
| `publisher_id=None` | All publishers; a supplied ID selects that publisher exactly |
| `tags=()` | No tag restriction; supplied normalized tags must all be present |
| `archived=False` | Visible packages, the default |
| `archived=True` | Only archived packages |
| `archived=None` | Both visible and archived packages |
| `customized=None` | Any customization state; true/false filters the existing status flag |
| `reconciliation_required=None` | Any review state; true/false selects pending/current packages |

Results are ordered by publisher/package identity. Search is a linear metadata
scan, without a database or separate persistent index. It observes each state
independently and is not a transaction across concurrent writers. It does not
verify content objects: use `check_store()` for integrity and availability auditing.

## Accepted descriptions and stored records

New registrations and accepted content revisions retain `content_metadata` in the
managed state: the content digest, pack name, and full pack description. Saves,
reconciliation, historical content restoration, content adoption, and duplication
update this metadata from the captured effective snapshot. Pending publisher
metadata does not replace the user's active description.

The metadata is tied to `active_digest`; state validation rejects stale digest
links. The integrity checker also compares names/descriptions against the actual
content objects. Descriptions share the normal state-record size limit. Capturing
metadata adds no caller-side publisher comparison or version-management work.

Older states can lack this metadata. Search never opens their bodies or rewrites
records to fill it silently. Their results have `pack_name=None`, `description=None`,
and `metadata_complete=False`. They can still match known IDs, labels, and tags.

`metadata_missing` lists candidate references that lack descriptive metadata after
non-text filters, even if the query returns no matching items. This avoids presenting
unknown descriptions as known non-matches. Each reference includes the state
revision needed for an explicit guarded refresh:

```python
for reference in report["metadata_missing"]:
    manager.package(reference["publisher_id"], reference["package_id"]).refresh_metadata(
        expected_revision=reference["revision"],
    )
```

`refresh_metadata()` reads/validates the accepted snapshot once, without contacting
the publisher or changing accepted content, baseline, tracking, or pending review.
It creates a history revision when metadata is missing or differs. An already-current
metadata refresh is a no-op; stale guards fail. Existing pins keep their original
state metadata. A publisher registration whose content/state is unchanged retains
its existing idempotent behavior and does not silently backfill old descriptions.

## Result and error contracts

`ManagedSearchResult` uses schema `prompt-manager/managed-search-1` and contains:

- `store`, original `query`, and matching `items` (`ManagedSearchEntry`).
- `issues` for state records that could not be read or validated.
- `metadata_missing` references and `metadata_complete` for readable candidates.
- `complete`, false when a record failed inspection or descriptive metadata is missing.

Entry fields include identity, state revision, content digest, resolved label,
nullable pack name/description, tags, archived/customized/review/tracking flags,
and descriptive-metadata completeness. These are metadata observations, not promises
that the referenced prompt will render or that its objects are intact.

Corrupt records are isolated rather than hiding healthy matches. A failure to
enumerate a managed directory raises `io` rather than reporting an exhaustive empty
library. Invalid query or
filter values raise `PromptError`; malformed state issues preserve domain/IO codes.
Search and tag operations create no third-party runtime dependency. Source/draft
registrations and standalone published releases remain outside managed search;
they retain their existing catalog/inspection APIs.

## CLI

```sh
prompt-manager managed set-tags publisher main --tag review --tag python --expected-revision STATE
prompt-manager managed set-tags publisher main --clear --expected-revision STATE
prompt-manager managed search 'parser review' --publisher publisher --tag python --customized --pending-review
prompt-manager managed search --archived
prompt-manager managed search --include-archived --unmodified --no-pending-review
prompt-manager managed refresh-metadata publisher main --expected-revision STATE
```

Repeated `--tag` values in `set-tags` define the replacement list; in `search` they
are all-required filters. Status filter opposites are mutually exclusive. Searches
return the SDK's JSON shape and exit 0 for a completed operation, including reports
with partial issues or missing metadata, matching catalog conventions. Scripts
requiring exhaustive metadata should inspect `complete`. Argument errors use exit
2; operational/validation exceptions use the existing JSON stderr envelope/exit 1.

## Stored compatibility

`tags` and `content_metadata` are optional additions to managed state/history under
`prompt-manager/managed-1`. Untouched stored states remain readable and are not
rewritten by search. New accepted content includes descriptive metadata automatically;
older strict readers must be upgraded to recognize these fields before reading
newly written records. SDK consumers should tolerate additional result fields.
Full-store archives preserve tags and descriptive metadata byte for byte.
