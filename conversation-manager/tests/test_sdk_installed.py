"""Pinned official SDK binaries against localhost model fixtures; no paid API calls."""

import asyncio, http.server, json, tempfile, threading, uuid, unittest, os, sys, hashlib
from pathlib import Path
from types import SimpleNamespace
from conversation_manager.adapters.sdk_session import SdkSession
from conversation_manager.adapters.antigravity_sdk import AntigravityDriver
from conversation_manager.adapters.claude_sdk import ClaudeDriver as NativeClaudeDriver
from conversation_manager.models import digest


def ClaudeDriver(settings, identity, directory, restore, callbacks, env, **options):
    cli = os.environ.get('AIXAND_TEST_EXTERNAL_CLAUDE')
    if not cli or not Path(cli).is_file():
        raise unittest.SkipTest('Select an external CLI with AIXAND_TEST_EXTERNAL_CLAUDE for installed SDK fixture tests')
    return NativeClaudeDriver(settings, identity, directory, restore, callbacks,
        {**env, 'AIXAND_CLAUDE_CLI': cli, 'DISABLE_AUTOUPDATER': '1'}, **options)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if hasattr(self.server, "auth_headers"):
            self.server.auth_headers.append({"authorization": self.headers.get("authorization"), "x-api-key": self.headers.get("x-api-key")})
        if "count_tokens" in self.path:
            body = json.dumps({"input_tokens": 1}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.server.calls += 1
        if self.server.calls > getattr(self.server, 'call_limit', 10):
            self.send_response(400)
            self.end_headers()
            return
        self.server.paths.append(self.path)
        self.server.requests.append(data)
        if getattr(self.server, 'scenario', None) == 'steering' and self.server.calls == 1:
            self.server.first_request.set()
            self.server.release_request.wait(25)
        tool_turn = (
            self.server.calls % 2 == 1
            and getattr(self.server, "scenario", "tools") == "tools"
        )
        tool_marker = getattr(self.server, 'tool_marker', 'captured_echo')
        arguments = getattr(self.server, 'tool_arguments', {'message': 'fixture tool'})
        response_text = getattr(self.server, 'response_text', 'fixture response')
        if getattr(self.server, 'probe', False):
            names = [row.get('name', '') for row in data.get('tools', [])]
            names += [row.get('name', '') for group in data.get('tools', [])
                      for row in group.get('functionDeclarations', group.get('function_declarations', []))]
            tool_turn = not self.server.probe_called and any(tool_marker in name or name == 'call_mcp_tool' for name in names)
            if tool_turn:
                self.server.probe_called = True
        if getattr(self.server, 'scenario', None) == 'workflow':
            tool_turn = self.server.calls == 2
        if getattr(self.server, 'scenario', None) == 'steering':
            tool_turn = self.server.calls == 1
        if self.server.kind == "antigravity":
            tool_name = next(
                (
                    row["name"]
                    for tools in data.get("tools", [])
                    for row in tools.get(
                        "functionDeclarations", tools.get("function_declarations", [])
                    )
                    if tool_marker in row["name"] or row["name"] == "call_mcp_tool"
                ),
                getattr(self.server, "fixture_tool", None),
            )
            self.server.fixture_tool = tool_name
            if tool_turn and tool_name is None:
                raise AssertionError("The fixture MCP tool was not declared")
            if tool_name == "call_mcp_tool":
                arguments = {
                    "ServerName": getattr(self.server, 'tool_server', 'managedfixture'),
                    "ToolName": tool_marker,
                    "Arguments": arguments,
                    "toolSummary": "Fixture call",
                    "toolAction": "Calling fixture",
                }
            part = (
                {
                    "functionCall": {
                        "name": tool_name,
                        "args": arguments,
                    }
                }
                if tool_turn
                else {"text": response_text}
            )
            payload = {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [part]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 1,
                    "candidatesTokenCount": 1,
                    "totalTokenCount": 2,
                },
            }
            body = "data: " + json.dumps(payload) + "\n\n"
        else:
            model = data.get("model", "claude-sonnet-4-6")
            events = [
                (
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_fixture_" + str(self.server.calls),
                            "type": "message",
                            "role": "assistant",
                            "model": model,
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 1, "output_tokens": 0},
                        },
                    },
                ),
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": response_text},
                    },
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                (
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": 1},
                    },
                ),
                ("message_stop", {"type": "message_stop"}),
            ]
            if tool_turn:
                tool_name = next(
                    row["name"]
                    for row in data["tools"]
                    if tool_marker in row.get("name", "")
                )
                events[1][1]["content_block"] = {
                    "type": "tool_use",
                    "id": "tool_fixture_" + str(self.server.calls),
                    "name": tool_name,
                    "input": {},
                }
                events[2][1]["delta"] = {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(arguments),
                }
                events[4][1]["delta"]["stop_reason"] = "tool_use"
            body = "".join(
                "event: " + kind + "\ndata: " + json.dumps(row) + "\n\n"
                for kind, row in events
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())


class Callbacks:
    def __init__(self):
        self.events = []
        self.permissions = []
        self.adapter = SimpleNamespace(
            core=SimpleNamespace(response_observed=lambda x: None)
        )

    def emit(self, kind, payload, **kw):
        self.events.append((kind, payload))

    async def permission(self, *a):
        self.permissions.append(a)
        return getattr(self, "allow", False)

    async def question(self, *a):
        return {}


@unittest.skipUnless(
    os.environ.get("MANAGER_SDK_INSTALLED_TESTS"),
    "Set MANAGER_SDK_INSTALLED_TESTS=1 with the pinned optional SDKs installed",
)
class InstalledSdkTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_claude_steering_reaches_the_post_tool_continuation(self):
        await self.steering('claude')

    async def test_real_antigravity_steering_reaches_the_post_tool_continuation(self):
        await self.steering('antigravity')

    async def steering(self, kind):
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.kind, server.calls, server.paths, server.requests, server.scenario = kind, 0, [], [], 'steering'
        server.first_request, server.release_request = threading.Event(), threading.Event()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root, identity = Path(temporary), str(uuid.uuid4())
                mcp = root / 'captured_mcp.py'
                mcp.write_text('from mcp.server.fastmcp import FastMCP\nserver = FastMCP("fixture")\n@server.tool()\ndef captured_echo(message: str) -> str:\n return message\nserver.run(transport="stdio")\n')
                settings = {'cwd': temporary, 'mcp': {'servers': [{'name': 'managedfixture', 'command': sys.executable, 'args': [str(mcp)]}]}}
                env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin')}
                if kind == 'claude':
                    env.update(ANTHROPIC_API_KEY='fixture-key', ANTHROPIC_BASE_URL=f'http://127.0.0.1:{server.server_port}', CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
                    cls = ClaudeDriver
                else:
                    env.update(GEMINI_API_KEY='fixture-key', GEMINI_API_BASE_URL=f'http://127.0.0.1:{server.server_port}')
                    cls = AntigravityDriver
                callbacks = Callbacks()
                callbacks.allow = True
                driver = cls(settings, identity, root, False, callbacks, env)
                actor = SdkSession(driver)
                pending = None
                try:
                    async with asyncio.timeout(40):
                        await actor.ready
                        pending = asyncio.create_task(actor.submit('prompt', 'Complete the fixture task.'))
                        while not server.first_request.is_set():
                            await asyncio.sleep(.02)
                        original_task = actor.prompt_task
                        await actor.submit('steer', 'STEERING_ADMISSION: focus on the current task.', 'stable-followup')
                        self.assertIs(actor.prompt_task, original_task)
                        self.assertFalse(pending.done())
                        server.release_request.set()
                        self.assertEqual((await pending)['status'], 'completed')
                        self.assertEqual(driver.identity, identity)
                        self.assertTrue(any('STEERING_ADMISSION' in json.dumps(row) for row in server.requests[1:]), server.requests)
                        self.assertTrue(any(kind == 'tool.result' for kind, _ in callbacks.events))
                finally:
                    server.release_request.set()
                    await actor.close()
                    if pending is not None:
                        await asyncio.gather(pending, return_exceptions=True)
        finally:
            server.release_request.set()
            await asyncio.to_thread(server.shutdown)
            server.server_close()

    async def test_real_claude_profile_login_discovers_models_sends_and_restores_without_an_api_key(self):
        for native_default in (False, True):
            with self.subTest(native_default=native_default):
                await self.claude_profile_login(native_default)

    async def claude_profile_login(self, native_default):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.kind, server.calls, server.paths, server.requests, server.scenario = "claude", 0, [], [], "text"
        server.auth_headers = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                profile = root / (".claude" if native_default else "selected-profile")
                profile.mkdir(mode=0o700)
                (profile / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
                    "accessToken": "fixture-oauth-access", "refreshToken": "fixture-oauth-refresh",
                    "expiresAt": 4102444800000, "scopes": ["user:profile", "user:inference"],
                    "subscriptionType": "max", "rateLimitTier": "default_claude_max_5x",
                }}))
                (root / ".claude.json" if native_default else profile / ".claude.json").write_text(json.dumps({"oauthAccount": {
                    "accountUuid": "11111111-1111-4111-8111-111111111111", "emailAddress": "fixture@example.test",
                }, "hasCompletedOnboarding": True}))
                identity = str(uuid.uuid4())
                env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root), "CLAUDE_CONFIG_DIR": "" if native_default else str(profile),
                       "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                       "ANTHROPIC_API_KEY": "competing-key-that-must-not-be-used"}
                for restore in (False, True):
                    driver = ClaudeDriver({"cwd": temporary, "model": "claude-sonnet-4-6"}, identity, root, restore, Callbacks(), env,
                                          authentication={"provider": "claude", "method": "profile-login", "id": "fixture-account"})
                    actor = SdkSession(driver)
                    try:
                        async with asyncio.timeout(30):
                            self.assertEqual(await actor.ready, identity)
                            self.assertTrue(driver.models, "The installed SDK should advertise models for the selected login")
                            before = server.calls
                            config = await actor.submit("configuration")
                            self.assertTrue(any(row.get("category") == "model" and row.get("options") for row in config["options"]))
                            self.assertEqual(server.calls, before, "Model discovery must not invoke inference")
                            await actor.submit("prompt", "Remember this local fixture." if not restore else "Recall this local fixture.")
                            self.assertEqual(server.auth_headers[-1]["authorization"], "Bearer fixture-oauth-access")
                            self.assertTrue(any(path.stem == identity for path in profile.glob("projects/**/*.jsonl")),
                                            [str(path.relative_to(root)) for path in root.rglob('*.jsonl')])
                    finally:
                        await actor.close()
                self.assertEqual(server.calls, 2)
                self.assertTrue(server.auth_headers)
                for headers in server.auth_headers:
                    self.assertEqual(headers["authorization"], "Bearer fixture-oauth-access")
                    self.assertIsNone(headers["x-api-key"])
                self.assertTrue(any(path.stem == identity for path in profile.glob("projects/**/*.jsonl")))
        finally:
            server.shutdown()
            server.server_close()

    async def test_antigravity_typed_plan_candidate_keeps_identity(self):
        # This checks transport and identity only. A text fixture cannot qualify
        # plan artifact feedback/approval, so Suite still marks /plan unavailable.
        from google.antigravity import Agent, LocalAgentConfig, types
        from google.antigravity.models import DEFAULT_MODEL

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.kind, server.calls, server.paths, server.requests, server.scenario = 'antigravity', 0, [], [], 'text'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            for behavior in (types.AgentBehavior.AUTONOMOUS, types.AgentBehavior.INTERACTIVE):
                with tempfile.TemporaryDirectory() as temporary:
                    identity = str(uuid.uuid4())
                    config = LocalAgentConfig(cwd=temporary, conversation_id=identity, save_dir=temporary+'/sessions', app_data_dir=temporary+'/app',
                        session_continuation_mode=types.SessionContinuationMode.CREATE_ONLY, api_key='fixture-key', max_iterations=2,
                        models=[types.ModelTarget(name=DEFAULT_MODEL, endpoint=types.GeminiAPIEndpoint(api_key='fixture-key', base_url=f'http://127.0.0.1:{server.server_port}'))],
                        capabilities=types.CapabilitiesConfig(agent_behavior=behavior))
                    async with asyncio.timeout(20):
                        async with Agent(config) as agent:
                            response = await agent.chat([types.SlashCommand(name=types.BuiltinSlashCommandName.PLAN), 'Plan a greeting function.'])
                            async for _ in response.chunks:
                                pass
                            self.assertEqual(agent.conversation_id, identity)
            self.assertEqual(server.calls, 2)
        finally:
            server.shutdown()
            server.server_close()

    async def exercise(self, kind):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.kind = kind
        server.calls = 0
        server.paths = []
        server.requests = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                identity = str(uuid.uuid4())
                cls = AntigravityDriver if kind == "antigravity" else ClaudeDriver
                root = Path(temp)
                skill = root / "audit"
                skill.mkdir()
                content = b"---\nname: audit\ndescription: MANAGER_CAPTURED_SKILL_DESCRIPTION\n---\nRead refs when reviewing.\n"
                (skill / "SKILL.md").write_bytes(content)
                manifest = {
                    "name": "audit",
                    "directories": [],
                    "files": [
                        {
                            "path": "SKILL.md",
                            "size": len(content),
                            "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
                            "executable": False,
                        }
                    ],
                }
                mcp = root / "mcp-fixture.py"
                mcp.write_text(
                    'from mcp.server.fastmcp import FastMCP\nserver=FastMCP("managed-fixture")\n@server.tool()\ndef captured_echo(message: str) -> str:\n return message\nserver.run(transport="stdio")\n'
                )
                settings = {
                    "cwd": temp,
                    "instructions": {"text": "MANAGER_OWNED_INSTRUCTION"},
                    "skills": [
                        {
                            "name": "audit",
                            "export": str(skill),
                            "digest": digest(manifest),
                            "manifest": manifest,
                        }
                    ],
                    "mcp": {
                        "servers": [
                            {
                                "name": "managedfixture",
                                "command": sys.executable,
                                "args": [str(mcp)],
                            }
                        ]
                    },
                }
                env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
                if kind == "antigravity":
                    env.update(
                        GEMINI_API_KEY="fixture-key",
                        GEMINI_API_BASE_URL=f"http://127.0.0.1:{server.server_port}",
                    )
                else:
                    env.update(
                        ANTHROPIC_API_KEY="fixture-key",
                        ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}",
                        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
                    )
                for restore in (False, True):
                    callbacks = Callbacks()
                    callbacks.allow = restore
                    driver = cls(
                        settings, identity, Path(temp), restore, callbacks, env
                    )
                    actor = SdkSession(driver)
                    try:
                        async with asyncio.timeout(40):
                            self.assertEqual(await actor.ready, identity)
                            before = server.calls
                            commands = {
                                row["name"]: row
                                for row in driver.commands()["commands"]
                            }
                            self.assertEqual(commands["context"]["execution"], "native")
                            if kind == "claude":
                                self.assertEqual(
                                    commands["compact"]["execution"], "native"
                                )
                                self.assertEqual(
                                    commands["manager-resources:audit"]["execution"],
                                    "native",
                                )
                                await actor.submit("set_mode", "plan")
                                await actor.submit("set_mode", "default")
                            for inspection in ("context", "usage", "mcp", "skills"):
                                self.assertIsInstance(
                                    await actor.submit("inspect_command", inspection),
                                    dict,
                                )
                            self.assertEqual(
                                server.calls,
                                before,
                                "Command discovery and inspection must not invoke the model",
                            )
                            result = await actor.submit(
                                "prompt", "Reply exactly fixture response."
                            )
                            self.assertEqual(result["status"], "completed", result)
                            self.assertTrue(driver.identity_observed)
                            text = "".join(
                                p["text"]
                                for k, p in callbacks.events
                                if k == "message.delta" and p["role"] == "assistant"
                            )
                            self.assertEqual(text, "fixture response")
                            self.assertEqual(
                                len(callbacks.permissions),
                                1,
                                "The SDK tool must await the managed approval callback: "
                                + str(callbacks.events)[:2000],
                            )
                            started = [
                                p["tool_call_id"]
                                for k, p in callbacks.events
                                if k == "tool.start"
                            ]
                            finished = [
                                p["tool_call_id"]
                                for k, p in callbacks.events
                                if k == "tool.result"
                            ]
                            self.assertEqual(len(started), 1)
                            self.assertEqual(started, finished)
                    finally:
                        await actor.close()
                self.assertEqual(
                    server.calls,
                    4,
                    "Each explicit turn needs a tool response and a text response; restore must not send extra prompts",
                )
                captured = json.dumps(server.requests[0])
                for marker in (
                    "MANAGER_OWNED_INSTRUCTION",
                    "MANAGER_CAPTURED_SKILL_DESCRIPTION",
                    "captured_echo",
                ):
                    self.assertIn(
                        marker,
                        captured,
                        "The real SDK must expose its captured resource to the model",
                    )
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()

    async def test_claude_bundled_runtime_streams_and_restores(self):
        await self.exercise("claude")

    async def test_antigravity_bundled_runtime_streams_and_restores(self):
        await self.exercise("antigravity")

    async def test_claude_command_only_output_and_compaction_events_are_retained(self):
        import claude_agent_sdk as sdk

        identity = str(uuid.uuid4())
        callbacks = Callbacks()

        class Client:
            async def query(self, text):
                self.text = text

            async def receive_response(self):
                yield sdk.SystemMessage(
                    "compact_boundary",
                    {"compact_metadata": {"trigger": "manual", "pre_tokens": 123}},
                )
                yield sdk.ResultMessage(
                    subtype="success",
                    duration_ms=0,
                    duration_api_ms=0,
                    is_error=False,
                    num_turns=0,
                    session_id=identity,
                    usage={},
                    result="Compaction completed",
                )

        with tempfile.TemporaryDirectory() as temporary:
            driver = ClaudeDriver(
                {"cwd": temporary}, identity, Path(temporary), False, callbacks, {}
            )
            driver.sdk, driver.client = sdk, Client()
            result = await driver.prompt(
                "/compact", {"name": "compact", "key": "command-key"}
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [
                payload["text"]
                for kind, payload in callbacks.events
                if kind == "message.delta"
            ],
            ["Compaction completed"],
        )
        self.assertEqual(
            [
                payload["metadata"]["pre_tokens"]
                for kind, payload in callbacks.events
                if kind == "session.compacted"
            ],
            [123],
        )
        self.assertEqual(
            [
                payload["key"]
                for kind, payload in callbacks.events
                if kind == "command.output"
            ],
            ["command-key"],
        )

    async def test_streaming_usage_keeps_the_latest_cumulative_estimate(self):
        import claude_agent_sdk as sdk

        identity = str(uuid.uuid4())

        class Client:
            async def query(self, text):
                self.cost = None if text == "missing" else float(text)

            async def receive_response(self):
                yield sdk.ResultMessage(
                    subtype="success",
                    duration_ms=0,
                    duration_api_ms=0,
                    is_error=False,
                    num_turns=1,
                    session_id=identity,
                    usage={"output_tokens": 1},
                    total_cost_usd=self.cost,
                )

        with tempfile.TemporaryDirectory() as temporary:
            driver = ClaudeDriver(
                {"cwd": temporary}, identity, Path(temporary), False, Callbacks(), {}
            )
            driver.sdk, driver.client = sdk, Client()
            for prompt, expected in (("0.1", 0.1), ("0.2", 0.2), ("missing", None)):
                await driver.prompt(prompt)
                usage = await driver.inspect_command("usage")
                self.assertEqual(usage["estimated_cost_usd"], expected)
                self.assertEqual(usage["cost_scope"], "sdk-session-cumulative")
                self.assertEqual(usage["last_turn"], {"output_tokens": 1})

    async def test_real_claude_compact_keeps_the_provider_identity(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.kind, server.calls, server.paths, server.requests, server.scenario = (
            "claude",
            0,
            [],
            [],
            "text",
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                identity = str(uuid.uuid4())
                callbacks = Callbacks()
                env = {
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "ANTHROPIC_API_KEY": "fixture-key",
                    "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                }
                driver = ClaudeDriver(
                    {"cwd": temporary}, identity, Path(temporary), False, callbacks, env
                )
                actor = SdkSession(driver)
                try:
                    async with asyncio.timeout(40):
                        await actor.ready
                        await actor.submit(
                            "prompt", "Remember this fixture project goal."
                        )
                        result = await actor.submit(
                            "prompt",
                            "/compact",
                            {"name": "compact", "key": "real-compact"},
                        )
                        self.assertEqual(result["status"], "completed", result)
                        self.assertTrue(driver.identity_observed)
                        output = next(
                            row
                            for kind, row in callbacks.events
                            if kind == "command.output" and row["key"] == "real-compact"
                        )
                        self.assertTrue(output["text"], output)
                finally:
                    await actor.close()
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
