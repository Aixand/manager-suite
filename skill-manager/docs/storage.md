# Storage and integration contract

`Manager` constructs without I/O writes. Its canonical store path identifies the
store with `sha256(canonical({"kind":"skill","home":absolute_home}))`. Canonical
JSON has sorted string keys, compact separators, finite values, and strict UTF-8.
Duplicate input keys and invalid Unicode are rejected. All digests use
`sha256:` plus 64 lowercase hexadecimal characters.

```text
HOME/
  sources/SOURCE.json
  .locks/store.lock
  blobs/BYTE_DIGEST_HEX
  objects/PACKAGE_DIGEST_HEX.json
  releases/SOURCE/NAME/VERSION.json
```

`skill-manager/source-1` core fields are `schema`, `id`, `paths`, `label`, `owner`,
`writable`, and `revision`. Revision hashes the core mapping without its own field.
Observation-only availability and discovery diagnostics are excluded. Registration
replacement/removal uses the observed revision; null means create only.

`skill-manager/entry-1` IDs hash `[store_id, source_id, absolute_skill_directory]`.
Draft inspection includes source identity, path, editability, status, metadata,
manifest, digest/kind, and an isolated error. `skill-manager/catalog-1` supplies
`store`, `store_id`, `sources`, and `entries`. A bad source does not hide good sources;
a bad skill does not hide its siblings. Discovery and source registration are distinct.

For a valid draft, the package digest hashes the content manifest. For malformed
frontmatter, the repair digest hashes exactly
`{schema:"skill-manager/draft-files-1", directories, files}`. Both include raw
`SKILL.md` byte identity. Unsafe/unreadable trees have diagnostics and a null digest.
Do not cache a draft digest as a release reference or managed launch selection.

Publication writes byte-addressed blobs and the manifest, checks the draft again,
then commits one immutable `skill-manager/release-1` record. Blob/object writes use
no-replace hard links and verify any existing content. Release record fields are
`schema`, `reference`, `source_id`, `source_revision`, `source_path`, `name`, `version`,
`digest`, and `provenance`; provenance captures the registration and publication time.
No automatic latest reference or publication advancement is implied. An interrupted
publication may leave unreferenced blobs/objects; they do not count as a publication.
No release relies on the current source descriptor or original draft directory.

`skill-manager/resolution-1` includes the validated release, content manifest, store
identity, digest, content-object path, and per-file blob paths. Consumers use the
public API, especially `export`, instead of trusting cached blob paths. Each resolution
checks strict JSON, manifest identity, portable paths, every blob's size/digest,
and metadata agreement with captured `SKILL.md`. Missing/corrupt objects or blobs
are integrity errors. Missing release references are not-found errors.

`skill-manager/diff-1` lists added and removed file rows, changed rows with both
byte/executable flags, added/removed directories, and metadata before/after.
`skill-manager/validation-1` reports validity and structured diagnostics without
executing instructions or scripts. `skill-manager/export-1` contains `reference`,
`digest`, `destination`, and `status` (`exported` or `identical`). All error objects
have `code`, `message`, `uncertain`, `operation_id`, and `recovery`.

A fake consumer fixture resolves an explicit reference, exports to its own parent,
and retains the reference, digest, and export receipt. Later Agent Manager integration
can use the same operations for immutable launch snapshots; activation must use a
verified native skill mechanism. This package makes no backend activation claims.
