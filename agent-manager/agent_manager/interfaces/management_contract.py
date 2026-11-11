"""Profile-only management contract shared by local MCP and SDK administration."""
from agent_manager.errors import Error
NAMESPACE = "aixand_agents"
INSTRUCTIONS = """Manage local native profiles only when requested. Resolve the provider and exact profile. Read revisions before editing. Each profile is a named alias of a stable environment; all aliases share its native home and preferences; the provider owns its login. Deleting a profile removes only its alias. Environment forget/purge is separate and guarded. Purge requires an explicit request and refuses referenced history. Login runs in the selected profile through the native CLI; never request tokens or copy credentials. Unsupported cross-profile continuation must be reported explicitly."""
NAME = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"}
REVISION = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}
UUID = {"type": "string", "format": "uuid"}

def tool(
    name, description, properties, required=(), *, readonly=False, destructive=False
):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": readonly,
            "destructiveHint": destructive,
            "openWorldHint": False,
        },
    }


IDENTITY = {"provider": NAME, "profile": NAME}
EDIT = {**IDENTITY, "expectedRevision": REVISION, "requestId": UUID}
TOOLS = [
    tool('get_configuration', 'Inspect provider/profile configuration and effective sources without launching a model.',
        {**IDENTITY, 'effective': {'type': 'boolean'}, 'refresh': {'type': 'boolean'}}, ('provider',), readonly=True),
    tool('get_launch_arguments', 'Read saved arguments and the revision for one provider or environment scope.', IDENTITY, ('provider',), readonly=True),
    tool('set_launch_arguments', 'Replace literal saved arguments in the selected scope using its observed revision.',
        {**EDIT, 'args': {'type': 'array', 'items': {'type': 'string'}}}, ('provider', 'args', 'expectedRevision')),
    tool('get_native_instructions', 'Read native instruction text, path and file revision.', IDENTITY, ('provider',), readonly=True),
    tool('set_native_instructions', 'Write native Markdown instructions using their observed file revision.',
        {**EDIT, 'text': {'type': 'string'}}, ('provider', 'text', 'expectedRevision')),
    tool('check_readiness', 'Diagnose installation, profiles, native sign-in and feature qualification; refresh explicitly queries native observations.',
        {**IDENTITY, 'refresh': {'type': 'boolean'}}, ('provider',), readonly=True),
    tool("list_providers", "List native provider capabilities.", {}, readonly=True),
    tool("list_profiles", "List native profiles without reading credentials.", {"provider": NAME, "query": {"type": "string"}}, readonly=True),
    tool("get_profile", "Inspect a profile and its revision.", IDENTITY, ("provider", "profile"), readonly=True),
    tool("create_profile", "Create a fresh native profile. Select its default explicitly.", {"provider": NAME, "name": NAME, "model": {"type": "string"}, "requestId": UUID}, ("provider", "name")),
    tool("connect_profile", "Connect an existing native directory without copying it.", {"provider": NAME, "name": NAME, "directory": {"type": "string", "minLength": 1}, "requestId": UUID}, ("provider", "name", "directory")),
    tool("set_default_profile", "Select the explicit default execution profile.", EDIT, ("provider", "profile", "expectedRevision")),
    tool("rename_profile", "Rename metadata; native files stay in place.", {**EDIT, "newName": NAME}, ("provider", "profile", "newName", "expectedRevision")),
    tool("duplicate_profile", "Copy Manager preferences to a fresh home without copying native credentials or history.", {**EDIT, "newName": NAME}, ("provider", "profile", "newName", "expectedRevision")),
    tool("delete_profile", "Remove only an alias; preserve its environment and native data.", EDIT, ("provider", "profile", "expectedRevision"), destructive=True),
    tool("check_profile", "Query the native CLI's public login status without reading tokens.", IDENTITY, ("provider", "profile"), readonly=True),
    tool('get_budget', 'Read account or paying-connection allowance for one native environment; aliases share observations.',
         {**IDENTITY, 'cwd': {'type': 'string'}, 'model': {'type': 'string'},
          'refresh': {'type': 'boolean'}, 'cached': {'type': 'boolean'}}, ('provider',), readonly=True),
    tool('get_integrations', 'Read native integrations, active public connection metadata and reviewed budget capabilities.',
         {**IDENTITY, 'cwd': {'type': 'string'}, 'refresh': {'type': 'boolean'}, 'cached': {'type': 'boolean'}}, ('provider',), readonly=True),
    tool('get_models', 'Read the daily native model catalog for an environment and project; listed models do not guarantee account access.',
         {**IDENTITY, 'cwd': {'type': 'string', 'minLength': 1}, 'refresh': {'type': 'boolean'},
          'cached': {'type': 'boolean'}}, ('provider',), readonly=True),
    tool('list_budgets', 'Read one account-allowance observation per environment, without summing shared accounts.',
         {'provider': NAME, 'cwd': {'type': 'string'}, 'model': {'type': 'string'},
          'refresh': {'type': 'boolean'}, 'cached': {'type': 'boolean'}}, ('provider',), readonly=True),
    tool("alias_profile", "Add a name to an existing profile's environment, without copying files.",
         {"provider": NAME, "name": NAME, "profile": NAME, "environmentId": UUID, "requestId": UUID}, ("provider", "name")),
    tool("list_environments", "List environments, including those without aliases.", {"provider": NAME}, ("provider",), readonly=True),
    tool("get_environment", "Inspect an environment and its preference revision.", {"provider": NAME, "environmentId": UUID}, ("provider", "environmentId"), readonly=True),
    tool("forget_environment", "Forget an unreferenced, idle environment; preserve native files.",
         {"provider": NAME, "environmentId": UUID, "expectedRevision": REVISION, "requestId": UUID}, ("provider", "environmentId", "expectedRevision"), destructive=True),
    tool("purge_environment", "Explicitly remove a verified owned home; refuse aliases, active runs and retained history.",
         {"provider": NAME, "environmentId": UUID, "expectedRevision": REVISION, "requestId": UUID}, ("provider", "environmentId", "expectedRevision"), destructive=True),
]
for _entry in TOOLS:
    if _entry['name'] == 'check_readiness': _entry['annotations']['openWorldHint'] = True
    if _entry['name'] in ('get_budget', 'list_budgets', 'get_models', 'get_integrations'):
        _entry['annotations']['openWorldHint'] = True
        _entry['inputSchema']['not'] = {'required': ['refresh', 'cached'], 'properties': {
            'refresh': {'const': True}, 'cached': {'const': True}}}
next(row for row in TOOLS if row['name'] == 'alias_profile')['inputSchema']['oneOf'] = [
    {'required': ['profile'], 'not': {'required': ['environmentId']}},
    {'required': ['environmentId'], 'not': {'required': ['profile']}},
]
NATIVE_TOOLS = [{"type": "namespace", "name": NAMESPACE, "description": "Manage native profiles", "tools": [
    {"type": "function", **{key: value for key, value in entry.items() if key != "annotations"}} for entry in TOOLS]}]
LOGIN_TOOLS = set()


def validate(tool_name, arguments):
    from jsonschema import Draft7Validator, FormatChecker

    entry = next((row for row in TOOLS if row["name"] == tool_name), None)
    if (
        entry is None
        or not isinstance(arguments, dict)
        or not Draft7Validator(
            entry["inputSchema"], format_checker=FormatChecker()
        ).is_valid(arguments)
    ):
        raise Error("Invalid profile management tool arguments")
