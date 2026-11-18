# Connect and operate resources, prompts, and tools

Use a consumer MCP client after resolving a definition. MCP Manager does not expose
a general-purpose tool-call, resource-read, or prompt-get facade. Its live connector
protocol provides capabilities, inventory, and reconnect only.

| Reader task | Owning interface |
| --- | --- |
| Resolve launch configuration | `Manager.resolve()` |
| Start stdio transport and initialize protocol | Consumer MCP client |
| List tools and call a tool | Client `list_tools()` and `call_tool()` |
| List/read resources | Client `list_resources()` and `read_resource()` |
| List/get prompts | Client `list_prompts()` and `get_prompt()` |
| Observe retained backend state | `Manager.runtime_status()` with an injected connector |
| Request selection-preserving reconnect | `Manager.reconnect()` |
| Close transport and child process | Consumer connection context |

Run the [local stdio example](examples/local-operations.md) to initialize a real
local MCP SDK fixture and exercise each operation. It uses no provider or model.
The fixture demonstrates the consumer boundary; it is not a production connector
or evidence for HTTP/OAuth or native provider attachment.

MCP Manager definitions currently support stdio (including the Sonar/Lore project
adapters). A remote transport needs a different implementation; no URL transport
field or OAuth credential store is accepted by the definition validator.

## Runtime operations and CLI results

See [runtime connectors](connectors.md) for the integration contract and
[managed definitions](MANAGED.md) for the owned resource lifecycle.

```sh
mcp-manager runtime capabilities --connector your_backend:factory --target @target.json --json
mcp-manager runtime status --connector your_backend:factory --target @target.json --scope session --json
mcp-manager runtime reconnect --connector your_backend:factory --target @target.json --scope session --operation-id request-1 --expected-selection-digest sha256:OBSERVED --json
mcp-manager runtime operation request-1 --json
```

`factory(options)` returns the connector; `--options JSON` is call-scoped.
Targets and connectors are explicit. Async `runtime_capabilities`, `runtime_status`
and `reconnect` accept the connector directly. `runtime_operation` inspects a
retained receipt locally. Reconnect preserves the selected configuration and
prepared generations. It does not refresh indexes, change application selections,
or hot-attach a server. Native process reload support needs adapter evidence; the
library makes no unconditional Codex/ACP reconnect claim.

Exit codes are 0 for success/accepted/completed, 2 for validation/conflict/not-found,
3 for unsupported, 4 for uncertain, and 1 for operational failure or a
failed/interrupted preparation job. Interrupting a waiting client reports a
recovery reference and leaves its retained job/intent available.


## Recover a reconnect request

Read capabilities before dispatch. Use the exact retained target, supported scope,
and observed selection digest. A server filter does not shrink process scope.
A completed reconnect receipt can still contain a failed connection observation;
an accepted receipt does not establish completion.

On lost delivery, inspect `runtime_operation(operation_id)` locally. Reuse the
original operation ID and intent. Changed intent conflicts; an uncertain receipt
must not be replayed under a new ID merely to seek a successful result.
The [connector contract](connectors.md) owns target and receipt fields.

See [structural interfaces](../src/mcp_manager/live/contracts.py),
[live normalization and receipts](../src/mcp_manager/live/service.py), and
[runtime tests](../tests/test_runtime.py).

Applications own resource selection and delivery. Agent Manager's native launch
uses provider configuration and does not attach MCP resources. Independent tools
can inspect [native targets](../../agent-manager/docs/resource-separation.md),
then implement their own authorized delivery outside both managers' current APIs.
