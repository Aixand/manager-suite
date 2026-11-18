# Standalone resource boundary

Agent Manager owns native environments, profile aliases, software installation,
authentication, agent configuration, execution and session continuation. It does
not own prompt/skill libraries, MCP servers, their attachments, reconciliation,
application resource composition or native resource delivery.

The management transport extra `mcp` and `agent-manager-mcp` expose agent
administration. Prompt, skill, and MCP server management belong to their respective
products. Native model/integration/allowance discovery is an agent capability.
Agent Manager operates independently of sibling products.

## Native execution

Native CLI and SDK launches use the provider's native configuration. Callers may
pass native CLI arguments or native thread settings. Resource composition and
delivery are the responsibility of independent tools.

`config instructions edit/show/path [--profile NAME]` operates on a native
instruction file. Omitting `--profile` uses the default profile. With no default,
select a profile explicitly. Launching an agent leaves shared instruction sections
and native guidance files untouched; edits are explicit.

Session continuation retains native identities, source history and workspace.
Resource assignments do not affect launches, continuation, environment duplication,
or preference revisions. User files remain untouched.

## Targets for independent tools

```sh
agent codex target list
agent codex target show ENVIRONMENT_UUID --json
```

`Manager.agent_targets()` and `resolve_agent_target(environment_id)` have async
`Client` equivalents. These observations require no native process, sign-in or
resource manager. A target identifies the provider, local registry path and
immutable environment UUID, with its native home, ownership, alias labels and
declarative provider capabilities. Capabilities are not a fresh installed-version
or connectivity check.

Aliases are display metadata. Unnamed environments remain discoverable. Retired
IDs fail resolution; consumers must never recreate them or retarget an attachment
using a reused alias. Registry paths are local instance locations, not portable
machine identities. Consumers must revalidate targets and coordinate concurrent
retirement before writing native files. Delivery adapters and destination managers
are separate work outside this product.

Independent resource tools use target discovery and native configuration through
the public APIs above.


## Legacy records and current public identifiers

Historical instruction/MCP/skill binding keys can remain in a registry as ignored
data. Agent Manager does not export or consume those assignments and does not
create a current resource-snapshot schema. Do not use old binding records as a
launch contract. Independent resource delivery remains the caller's responsibility.

The exported `ResourceError` name remains an alias of `OperationError` for structured
administrative and observation failures. Its name does not restore resource
assignment APIs. Native `mcp_servers` settings supplied through qualified Codex
`native_config` are explicit native configuration, not a managed resource binding.

See [public exports](../agent_manager/__init__.py), [error definitions](../agent_manager/errors.py),
[native settings validation](../agent_manager/runtime/native/configuration.py), and
[standalone boundary tests](../tests/test_standalone_boundary.py).
