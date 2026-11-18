# Configure a native launch

Set literal native arguments and inspect their effective order without changing provider-owned configuration implicitly.

## Save arguments at the intended scope

From your project directory, after configuring `main`:

```sh
agent codex config args add --profile main -- --model MODEL
agent codex config args list --profile main --json
agent codex config show --profile main --effective --json
```

Replace `MODEL` with a native model identifier. Without `--profile`, argument mutations target provider defaults. Profile arguments belong to the environment, so all aliases share them.

| Order | Layer |
| --- | --- |
| 1 | Provider `run_args` |
| 2 | Provider `launch_args` |
| 3 | Environment/profile arguments |
| 4 | Per-launch model and forwarded arguments |

Tokens remain ordered; the native CLI decides repeated-option behavior. Manager does not merge option/value pairs. Remove the exact saved token sequence when finished:

```sh
agent codex config args remove --profile main -- --model MODEL
```

These arguments affect terminal run/start/continue, including detached launches. Login and observation probes remain separate.

## Edit native instructions explicitly

```sh
agent codex config instructions show --profile main
agent codex config instructions edit --profile main
```

The editor uses a revision-checked draft and preserves it after failure. Omitting the profile selects the default; it does not create one. Native instruction edits do not synchronize libraries or redistribute shared guidance. Running providers can retain already-loaded instructions.

## Handle revisions and unknown defaults

Use the revision from the dedicated argument or instruction getter for writes to that scope. The aggregate effective-configuration revision is different. MCP setters require `expectedRevision` where declared.

Effective configuration does not expand vendor project/global settings: `native_defaults_resolved` is false. A null native model is unknown, not proof that none exists. Refresh affects subsequent work; accepted in-flight launches retain their captured provider settings.

The [full configuration reference](configuration.md) owns preference keys, configuration path precedence, model observations, login diagnostics, and exact schemas. [Saved-argument tests](../tests/test_saved_args.py) and [configuration service tests](../tests/test_configuration_services.py) verify these boundaries. Use [storage paths](storage.md) before selecting an existing context.
