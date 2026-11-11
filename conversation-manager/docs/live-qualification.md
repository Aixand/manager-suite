# Qualify a live provider

Use these checks to exercise a provider with real model requests. They are
separate from offline package tests and can consume account allowance. Run from
`conversation-manager/` with its development environment prepared; see
[development](development.md).

## Codex

Use an existing signed-in environment UUID. Choose a model available to that
account and a new report path:

```sh
.venv/bin/python scripts/qualify_codex_live.py --help
.venv/bin/python scripts/qualify_codex_live.py --allow-live --environment-id ENVIRONMENT_UUID --model MODEL --output REPORT
```

The check requires explicit live authorization through `--allow-live`.
`--controls` adds cancellation and permission-denial checks in a disposable workspace.

## OpenCode

Select an available provider/model route and a new report path:

```sh
.venv/bin/python scripts/qualify_opencode_live.py --help
.venv/bin/python scripts/qualify_opencode_live.py --allow-live --model PROVIDER/MODEL --output REPORT
```

## Interpret the result

Review the report and command exit status. Startup, attachment, inference,
command execution, and cleanup are separate outcomes. A quota or authentication
failure does not qualify inference, even when worker startup succeeds. Offline
fixture tests do not establish live provider support or native Windows behavior.
Retain reports with the tested revision, runtime versions, and platform in build
or review artifacts rather than copying test totals into product documentation.

For frontend behavior and recovery, use the [reference chat example](../examples/reference_chat/README.md).
For exact options and cleanup behavior, inspect the
[Codex checker](../scripts/qualify_codex_live.py) and
[OpenCode checker](../scripts/qualify_opencode_live.py).
