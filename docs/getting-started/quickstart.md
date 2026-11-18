# Verify local setup with a prompt

Create and verify one local resource without provider credentials, a model or a background service.

## Prerequisites

Complete the [full-suite source setup](installation.md#set-up-the-complete-suite) and activate `.venv`. Run commands from the `manager-suites` repository root. This tutorial uses a new `.manager-demo/prompts` store, separate from your default data.

## Create an owned prompt

```sh
prompt-manager --home .manager-demo/prompts managed create review --content 'Review the supplied work carefully.'
```

The result identifies publisher `local` and prompt package `review`. Creation is create-only: if that name already exists, reuse the existing store or choose a fresh tutorial home.

## Render the accepted prompt

```sh
prompt-manager --home .manager-demo/prompts managed render local review review
```

Expect the rendered content to include:

```text
Review the supplied work carefully.
```

The first `review` selects the managed package; the second selects the prompt inside it. Rendering is local composition. It does not submit a model request.

## Verify through Python

Save this as `check-prompt.py` in the repository root, then run `python check-prompt.py`:

```python
from prompt_manager import Manager

manager = Manager(home='.manager-demo/prompts', packs=[])
result = manager.render_managed('local', 'review', 'review', format='messages')
assert result['output'][0]['content'] == 'Review the supplied work carefully.'
print('Prompt verified')
```

Expect `Prompt verified`. You can now use the returned message list in an application that owns its model integration.

## Cleanup

No worker or provider was started. Keep `.manager-demo/prompts` to reuse the accepted prompt, or remove only that tutorial directory and `check-prompt.py` when finished. Default manager stores are untouched.

Next, try [connecting a skill to a project](../examples/connect-skill.md) or [resolving an MCP definition](../examples/resolve-mcp.md). If the command or import is missing, activate the environment used during setup.
