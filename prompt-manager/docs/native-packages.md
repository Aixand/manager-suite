# Share lifecycle policy with a native package backend

Use `PackageLifecycle` when a consumer owns another package format and needs
accepted-content policy. It does not convert native bytes into prompt packs.
`PACKAGE_LIFECYCLE_VERSION` is 1.

## Assign the backend responsibilities

| Owner | Responsibilities |
| --- | --- |
| Trusted backend | Native capture/validation, identifiers, digests, immutable content, records, locks, metadata description |
| Shared lifecycle | Publisher ordering, customization, reconciliation, history, holds, tracking, organization, metadata search |
| Optional delivery backend | Native release references and safe immutable export |
| Consumer | Construct the backend, acquire sources, expose review, maintain native stores |

The dependency points toward Prompt Manager. It imports no consumer backend and
does not discover plugins from stored records. Backend objects are trusted Python
code, not a sandbox. Constructing the lifecycle performs no IO.

## Use the prompt backend first

This excerpt uses Prompt Manager's existing native prompt format:

```python
from prompt_manager import Manager

manager = Manager(home="/absolute/prompt-store", packs=[])
lifecycle = manager.lifecycle()
status = lifecycle.ensure_registered("publisher", "review", "/absolute/prompt-pack",
                                     publisher_revision=1)
owned = lifecycle.resolve("publisher", "review")
```

Rendering remains a Manager/Package operation. An external format instead supplies
`PackageLifecycle(backend)` with the complete exported `PackageBackend` protocol.
Read the [backend contract](package-lifecycle.md#backend-contract) before implementing it.

## Preserve the safety boundaries

All writers must share the backend lock. Methods called under that lock must not
reacquire a non-reentrant lock. Publish verified immutable content before current
state can reference it. Keep native content digests distinct from managed-state
revision hashes. Validate a complete candidate before activation.

The comparison tree must preserve meaningful native state. Binary payload pointers
returned by `atomic_paths()` require explicit conflict choices; they never text
merge. Delivery operations require `PackageDelivery`; missing support raises
`unsupported`, without conversion fallback.

Use separate homes for incompatible formats unless the backend explicitly
validates/distributes every record. `Manager.check_store()`, backup, and restore
validate prompt stores only. An external backend must implement its own maintenance.
No remote acquisition, automatic migration, store relocation, or format switch
is supplied by this SDK.

See the retained [full package lifecycle reference](package-lifecycle.md) for optional
hooks, guarded acquisition records, binary trees, delivery, and compatibility.
The [protocol implementation](../src/prompt_manager/packages.py) and
[adapter tests](../tests/sdk/test_package_lifecycle.py) provide concrete evidence.
