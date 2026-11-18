# Resolve a definition without launching it

Use this path to verify a definition before giving it to a client. This guide
covers the development branch. You need Python 3.11+ and MCP Manager installed in
your environment. Run shell commands from the suite root.

```sh
python -m pip install ./mcp-manager
python mcp-manager/examples/resolve_without_launch.py
```

The example creates a temporary store, registers a script that would fail if
launched, and resolves it. It prints `PASS: resolved without launch` after checking
that no launch marker exists. The temporary store and script are removed on exit.
See the [example explanation](examples/resolve-without-launch.md).

For your own server, replace the executable and script paths below with installed
software. These commands register and resolve configuration; they do not run it.

```sh
mcp-manager --home .manager-demo/mcp add docs --definition '{"command":"python3","args":["/absolute/path/to/server.py"],"env_vars":["DOCS_TOKEN"]}' --json
mcp-manager --home .manager-demo/mcp catalog --json
mcp-manager --home .manager-demo/mcp resolve --servers '["docs"]' --cwd . --no-project --json
```

Expect a `configured` state and an absolute executable in `servers.docs`.
`missing-executable` means the executable cannot be found in the current environment.
Install or correct it before connecting. An unknown name raises `not_found`; use
`Manager.inspect()` to retain results for other names in a mixed selection.
Remove only the `.manager-demo` directory you created when finished.

Read [resolution and revisions](resolution.md) before editing an existing name.
Use the [local connected example](examples/local-operations.md) to test actual
MCP operations separately.

## Install the CLI

As an alternative to downloading a release archive, use the hosted beta installer
on Linux/macOS:

```sh
curl -fsSL https://installer.aixand.org/manager/mcp | sh
```

It bundles Python and dependencies, asks for confirmation, and keeps software
separate from existing manager data. See
[hosted installation](../../docs/release-installation.md#hosted-installer-alternative)
for supported targets, Windows PowerShell, script review, and custom stores.
Python API consumers install the package in their own application's environment.
