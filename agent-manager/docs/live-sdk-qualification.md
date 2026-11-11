# Live Codex SDK qualification

This check is separate from offline package qualification and ordinary tests.
It uses an existing signed-in Codex profile and an already running Undercurrent
daemon. It can consume allowance: a complete run submits three small model turns.
It does not install providers, sign in, modify profile settings, start/stop the
shared daemon, or stop unrelated executions.

Run with a Python environment containing `aixand-agent-manager[sdk]`:

```sh
python scripts/qualify_sdk_live.py --profile main --preflight-only
python scripts/qualify_sdk_live.py --profile main --allow-live
```

Use `--config FILE`, `--root STATE_ROOT`, `--runtime-home HOME`, and `--bridge PATH`
when needed to connect to the same registry and daemon as your application.
`--model ID` and `--reasoning-effort low` are optional per-launch overrides; they
do not persist changes to the profile. `--timeout SECONDS` bounds each response or
readiness check. `--output NEW_DIRECTORY` selects a fresh artifact directory.
The command refuses to overwrite a previous directory. Without it, a private
`agent-sdk-live-*` temporary directory is created and printed.

## Flow and evidence

1. Inspect version, sign-in, qualified features, fresh allowance, and profile
   selection. With only one environment this verifies selection, not ranking
   across accounts. Selection is observed without launching the chosen account;
   actual execution uses the explicit `--profile`.
2. Launch in a disposable project with a retained UUID. Wait for Undercurrent
   readiness and retry the same intent to verify the execution identity is unchanged.
3. Ask a small arithmetic question while retaining an ordered digit sequence in
   the conversation. The expected answer is absent from the prompt, so an echoed
   prompt cannot satisfy the response check. Also require runtime readiness after
   delivery; delivery alone is not a completed task.
4. Close both connections, recover the saved execution reference from another
   Python process, reconnect, and ask a second arithmetic question.
5. Stop that execution. Discover its native saved session using the project folder,
   continue it with the same profile and a new launch UUID, then ask for the earlier
   digits concatenated. That expected answer was absent from earlier prompts and
   replies, providing evidence of retained conversation context.
6. Stop the continuation. Cleanup recovers only this invocation's recorded references
   and launch UUIDs, including uncertain launches. It never enumerates sessions to
   stop them. If cleanup cannot establish termination, the report fails and retains
   recovery identities.

`report.json` contains check results, versions, allowance percentages, execution
IDs, launch UUIDs, failure stage/type, and cleanup status. `reference.json` contains
an exact execution reference. Reports do not contain credentials, prompts, model
answers, or transcript contents. Codex and Undercurrent still retain their normal
native history and terminal output; the script does not erase those stores.

Success exits zero; any failed check or incomplete cleanup exits nonzero. Preflight
success is explicitly `preflight-passed`, not a successful execution qualification.
Failures retain evidence and do not automatically send another prompt or launch
with a new key. Investigate the report and original run before repeating a test.
`RunNotFoundError` for an acknowledged reference is a cleanup failure, not proof
that its process stopped.

## Cross-profile continuation

Use `--continue-profile` to select a distinct destination environment:

```sh
python scripts/qualify_sdk_live.py --profile source --continue-profile destination --allow-live
```

Preflight requires distinct signed-in environments. Review retained-context checks
and cleanup in the generated report. A successful preflight does not qualify
inference or continuation. Reports apply to the tested provider versions and
platform; keep individual run results in review artifacts.

See the [qualification script](../scripts/qualify_sdk_live.py) for exact checks and
[continuation constraints](file-session-continuation.md) for admission rules.
