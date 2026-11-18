# Run a first conversation

Install the package, verify an offline worker, then choose an authenticated native environment only when you intend provider work.

## Verify without a provider

Run from `conversation-manager/` using Python 3.11–3.14 in an isolated environment:

```sh
python -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m conversation_manager demo --fixture --json
```

On Windows use `.venv\Scripts\python.exe`. The demo explicitly injects
`conversation_manager.fixture_sdk:factory`; it is not an authenticated Claude SDK.
Expect `status: completed`, `reattached_same_runtime: true`, and a completed turn
among the events. The demo terminates its worker and removes its temporary store.
If startup fails, inspect the returned receipt rather than assuming inference ran.

For executable assertions, use the [offline retained-evidence example](examples/offline-lifecycle.md).

## Install the CLI


As an alternative to downloading a release archive, use the hosted beta installer
on Linux/macOS:

```sh
curl -fsSL https://installer.aixand.org/manager/conversation | sh
```

It bundles Python and dependencies, asks for confirmation, and keeps software
separate from existing manager data. See
[hosted installation](../../docs/release-installation.md#hosted-installer-alternative)
for supported targets, Windows PowerShell, script review, and custom stores.
Python API consumers install the package in their own application's environment.


## Start an authenticated conversation

Install `.[agents,native-codex]` in the consumer environment for Agent Manager
and Codex integration. Configure and sign in to the native provider through
Agent Manager first. Follow [frontend startup](frontend-startup.md) with an exact
agent reference, absolute workspace and persisted operation ID. Discovery reports
implemented support; only actual inference establishes model access.

The [full frontend reference](frontend-api.md#python-interface) retains the Python
integration and CLI commands. Use [reference chat](examples/reference-chat.md) to
inspect actual UI behavior with the offline fixture before enabling live discovery.
