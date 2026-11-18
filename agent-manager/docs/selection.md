# Select an account and model

Choose an explicit environment or let startup rank eligible environments within one provider. Selection does not reserve remote quota.

## Inspect before choosing

From your project directory, with a configured native profile:

```sh
agent codex budget --all --refresh
agent codex models --profile main
agent codex config show --profile main --effective --json
```

Refresh can contact the provider through its native login. For offline inspection, SDK `budgets(cached=True)` and `models(cached=True)` avoid a fresh observation. Treat unavailable and stale allowance as unknown, not zero. Model catalogs describe observed routes; catalog presence does not establish entitlement.

[Configuration](configuration.md#budget-observations) owns observation fields, scope, freshness, and provider differences. [Automatic startup](configuration.md#automatic-startup) owns CLI policy.

## Choose the SDK policy

These are excerpts inside a connected `Client` context:

```python
explicit = await agents.spawn(profile='main', cwd='/project')
selected = await agents.start(cwd='/project', idempotency_key=retained_uuid)
print(selected.selection.reason)
```

`spawn` uses the requested profile without a budget query. `start` chooses the highest usable limiting-window percentage where comparable ranking is supported. Other providers use their declared default policy. Aliases share an environment observation. Account percentages are not compared across providers or incompatible model routes.

The default `fallback='error'` raises `SelectionUnavailable` when selection cannot establish an eligible profile. Explicit `fallback='default'` permits an unqualified default launch and records the reason. Neither path creates profiles or changes the configured default.

## Retain the decision

Save a UUID before dispatch. Reuse it with identical arguments after an uncertain reply. Keyed recovery retains the original selection instead of reranking. Conflicting model settings fail; changing launch arguments under an existing key conflicts.

Selection uses the requested workspace and is checked again under execution guards. An accepted key whose selection became invalid before dispatch needs a new key for a fresh decision. Resolve uncertain dispatch first.

See [SDK contract](sdk-contract-v1.md), [selection implementation](../agent_manager/profiles/selection.py), [automatic journal](../agent_manager/runtime/automatic.py), and [selection tests](../tests/test_automatic_selection.py).
