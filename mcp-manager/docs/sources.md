# Import sources and install captured generations

Use discovery to review source content before accepting definitions. Imports and
tracking retain configuration; installation is a separate explicit operation that
can execute package/build code.

## Source imports and installations

Source discovery captures immutable bytes before import. Import and tracking do
not execute packages. Installation is an explicit retained operation and does
not accept a pending publisher update automatically.

```sh
mcp-manager managed discover https://github.com/owner/repository --json
mcp-manager managed import-discovery sha256:DISCOVERY --select server.json --json
mcp-manager managed install PUBLISHER SERVER --expected-revision sha256:STATE --json
mcp-manager managed source-job UUID --json
```

Supported recipes are exact npm/PyPI versions and captured Node/Python source.
Node source requires a lockfile; Python uses an isolated environment and retained
wheels. Host runtimes remain prerequisites. Failed installation leaves the prior
usable generation available. Historical configurations keep their installation
recipes and can resolve the original generation.

Stored accepted configurations, source/discovery objects, installation manifests,
and job signatures remain readable. Completed request keys return their recorded
results without replay. Stored-record fixtures verify reads without external
service imports or changes to the recorded bytes.

For an offline distribution check, provide a wheelhouse containing MCP Manager
and both utility wheels, then run:

```sh
python tests/verify_installation.py --wheelhouse /path/to/wheels
```

The verifier installs only those three distributions into a disposable environment,
enforces worker dependency boundaries, and exercises source updates,
stored records, captured-source installation, and the installed CLI. Node fixture
coverage is skipped when Node/npm are unavailable.


## Owned publisher artifacts

Python integrations can call `Manager.capture_artifact(bytes_or_path)` to retain
an immutable, digest-named executable bundle under the MCP library's `artifacts`
directory. The returned descriptor contains `path`, `digest`, and `size`; capture
does not run the artifact or install dependencies. Repeated identical captures
reuse the owned file. Publishers can reference that path in registered definitions
so later source changes cannot silently replace an accepted executable.


## Review before activation

1. Discover a public GitHub source or use `Manager.import_local(path)` for a local definition.
2. Review the captured candidates and select the intended paths.
3. Inspect the accepted definition, recipe, and required host runtime.
4. Install explicitly, then inspect its retained source-job result.
5. Review pending publisher changes with [reconciliation](MANAGED.md) before activation.

Local imports copy definitions; they do not establish continuous filesystem
tracking. `source_capabilities()` advertises public GitHub, retained source jobs,
installation, and stdio transport; private GitHub is false. Detection of Node/Python
launch metadata does not establish MCP protocol compatibility.

`check_due_sources()` must be invoked by a consumer. It skips pinned sources and
paused tracking, and starts retained checks using the source interval (default 24
hours). It does not create an always-running scheduling service.

See [source service](../src/mcp_manager/sources/service.py),
[repository operations](../src/mcp_manager/sources/repository.py),
[installation service](../src/mcp_manager/installations/service.py),
[source tests](../tests/test_sources.py), and
[installation tests](../tests/test_installations.py).
