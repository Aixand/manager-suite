# Pack format, version 1

A pack is a directory containing `pack.json` and UTF-8 asset files. The
[demo pack](../packs/demo/pack.json) and [common pack](../packs/common/pack.json)
are executable examples. All object fields documented below are closed: unknown
fields are rejected.

## Manifest

```json
{
  "schema": "prompt-manager/pack-1",
  "name": "example",
  "description": "An optional description",
  "dependencies": {},
  "sections": {
    "task": {
      "file": "sections/task.md",
      "inputs": {"request": {"type": "string"}}
    }
  },
  "documents": {"guide": {"file": "documents/guide.md"}},
  "prompts": {
    "review": {
      "inputs": {
        "request": {"type": "string"},
        "include_guide": {"type": "boolean", "default": true}
      },
      "sections": [
        {
          "id": "task",
          "section": "task",
          "role": "user",
          "inputs": {"request": {"input": "request"}}
        },
        {
          "id": "guide",
          "document": "guide",
          "heading": "Reference material",
          "when": {"input": "include_guide", "equals": true}
        }
      ]
    }
  },
  "examples": [
    {
      "name": "basic-review",
      "prompt": "review",
      "inputs": {"request": "Review the parser"},
      "expect": {
        "contains": ["Review the parser"],
        "section_ids": ["task", "guide"]
      }
    }
  ]
}
```

`schema`, `name`, and `prompts` are required. Other top-level fields are optional.
A shared-only pack can have an empty `prompts` object. A prompt must have a
nonempty `sections` list; its `inputs` and `description` are optional.

Pack, dependency alias, asset, prompt, section-entry, and example names match
`[a-z][a-z0-9_-]{0,63}`. Version labels match
`[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}`. Releases use `pack@version`; prompt references
append `/prompt`. A digest can replace a version label.

Asset paths are normalized relative paths without `..`. Resolved symlinks must
stay inside the pack directory. Each asset declares a `file`; sections may also
declare `inputs`. Documents have no template inputs.

## Inputs and substitution

Prompt inputs and section inputs use the same small contract:

```json
{
  "request": {"type": "string"},
  "context": {"type": "object", "default": {}},
  "detail": {"type": "string", "enum": ["short", "full"], "default": "short"}
}
```

Types are `string`, `integer`, `number`, `boolean`, `object`, `array`, and `null`.
An input without a default is required. Undeclared supplied inputs are errors.
Defaults and enum members must match their declared type. Boolean values are not
integers; integer inputs reject floating-point values. Objects and arrays are
validated by their outer type, without nested schema rules. Each input may have
an optional text `description`.

Input names match `[a-zA-Z_][a-zA-Z0-9_]*`. Section Markdown uses:

```text
Task: {{ request }}
Project data: {{ context | json }}
```

Unformatted substitution requires a string. `| json` supports all JSON types and
uses compact JSON with sorted object keys. Spaces inside expressions are allowed.
No other expressions, filters, file includes, or executable code are supported.
To include literal `{{ ... }}` examples, use a reference document or an input
value. Inserted input text is not evaluated again.

Library inputs must be JSON values: string-keyed objects, lists, strings, finite
numbers, booleans, or null. Tuples and non-string object keys are rejected rather
than silently converted.

## Composition

Each entry has a unique `id` and exactly one `section` or `document` reference.
Local references are asset names. Dependency references are `alias/asset`.
Section and document names are resolved in separate namespaces.

An entry can set:

- `role`: `system`, `user`, or `assistant`; defaults to `user`.
- `heading`: literal single-line text, rendered as `## Heading`.
- `when`: `{ "input": "flag", "equals": true }` or another scalar comparison.
- `inputs`: section input bindings; unsupported for documents.

Each section binding selects a prompt input or a literal JSON value:

```json
{
  "request": {"input": "request"},
  "audience": {"value": "maintainers"}
}
```

Required section inputs need explicit bindings. Section defaults are applied when
a binding is absent. Binding types must match; an integer can supply a number.
Section enums are checked against actual bound values during rendering.

Conditions compare a declared scalar prompt input with a typed literal. Equality
uses canonical JSON values; for example `1` and `1.0` differ. Definitions are
validated even for entries omitted by a particular render. Products compute
business decisions and pass the resulting flags or values to these conditions.

Sections do not recursively include other sections. Reuse happens through the
prompt's ordered list. Shared packs may depend on other shared packs; a repeated
pack name along a dependency chain is rejected as a cycle.

## Shared dependencies and snapshots

```json
{"dependencies": {"common": "common@1"}}
```

The referenced release must already exist locally when loading a draft. A
snapshot embeds the exact resolved dependency digest and content, transitively.
Released packs use that embedded content without resolving labels again.

Changes to any manifest field, saved example, asset, or dependency snapshot change
the snapshot digest. Version labels live separately from the snapshot, so two
labels can intentionally identify the same content. Published labels are immutable.

## Rendering examples

Each example declares `name`, `prompt`, `inputs`, and a nonempty `expect` object.
Supported expectations are:

| Field | Assertion |
| --- | --- |
| `contains` | Every listed string occurs in rendered plain text |
| `excludes` | None of the listed strings occurs in rendered plain text |
| `section_ids` | Included section IDs exactly match this ordered list |
| `text` | Plain text exactly matches the supplied string |
| `messages` | Message output exactly matches the supplied array |

`validate` and `release` run all examples in the selected pack. Dependency examples
were checked when their own releases were created. A pack may have zero examples;
the command reports the names that actually passed. Ordinary `render` validates
definitions and actual inputs without rerunning saved examples.

## Output and identity

Each included entry produces one role/content message in declaration order.
Trailing newline characters are removed from its body; other whitespace is
preserved. A heading, when present, precedes the body with a blank line.

Plain text flattens those messages as `[role]`, a newline, and the message body,
with two newlines between messages and a final newline. When every conditional
entry is omitted, messages are `[]` and text is empty.

`--json` returns a `prompt-manager/render-result-1` object containing the output,
format, renderer version, prompt identity, resolved release, direct dependency
identities, input/output digests, and included/omitted section trace. Each trace
entry identifies the asset's owning snapshot digest. Transitive dependencies can
be inspected in the stored snapshot object.

Drafts and direct digest references have `release.version: null`; their content
digest is still present. A released label also records its version. Effective
inputs include applied prompt defaults before hashing. Section defaults are
captured in the pack snapshot. No raw-input history is saved automatically.

All digests use SHA-256 over UTF-8 JSON serialized with sorted object keys,
`ensure_ascii=False`, compact separators, and finite numbers only. Text output
is hashed as a JSON string, and message output as a JSON array. These rules are
versioned by `prompt-manager/render-1`; this format does not claim conformance to
an external JSON canonicalization standard.
