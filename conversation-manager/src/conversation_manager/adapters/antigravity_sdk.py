"""Google Antigravity SDK driver with explicit credentials and saved state."""

import os

from ..models import ConversationError
from .sdk_resources import mcp_servers


class AntigravityDriver:
    def __init__(self, settings, identity, directory, restore, callbacks, env):
        self.settings, self.identity, self.directory, self.restore = (
            settings,
            identity,
            directory,
            restore,
        )
        self.callbacks, self.env = callbacks, env
        self.agent, self.response = None, None
        self.entered = False
        self.identity_observed = False
        self.cancel_requested = False
        self.started_tools, self.finished_tools = set(), set()

    async def start(self):
        from ..sdk_runtime import activate
        activate(self.env.get('AIXAND_PROVIDER_SDK_PATH'))
        from google.antigravity import Agent, LocalAgentConfig, types
        from google.antigravity.hooks import hooks, policy

        self.types = types
        api_key = self.env.get("GEMINI_API_KEY")
        vertex = self.env.get("GOOGLE_GENAI_USE_VERTEXAI") == "true"
        if not api_key and not (
            vertex
            and self.env.get("GOOGLE_APPLICATION_CREDENTIALS")
            and self.env.get("GOOGLE_CLOUD_PROJECT")
        ):
            raise ConversationError(
                "authentication",
                "Antigravity SDK requires a selected Gemini API key or explicit Google Cloud credential reference",
            )
        variables = {key: "" for key in os.environ}
        variables.update(self.env)
        variables["HOME"] = str(self.directory)

        async def approval(call, reason=""):
            if call.id:
                self.start_tool(call)
            if call.name == "schedule" or any(
                call.args.get(key)
                for key in (
                    "IsDaemon",
                    "run_in_background",
                    "background",
                    "is_background",
                )
            ):
                allowed = False
            elif (self.settings.get("access") or {}).get(
                "permission_mode", "ask"
            ) == "deny":
                allowed = False
            else:
                allowed = await self.callbacks.permission(
                    str(call.name), call.args, call.id
                )
            if not allowed and call.id:
                self.finish_tool(
                    types.ToolResult(
                        id=call.id,
                        name=call.name,
                        error="Denied by managed permission policy",
                        result={"denied": True},
                    ),
                    observed=False,
                )
            return allowed

        @hooks.post_tool_call
        async def tool_finished(result):
            if result.id:
                self.finish_tool(result)

        @hooks.on_interaction
        async def interaction(spec):
            questions = [
                {
                    "id": str(index),
                    "question": row.question,
                    "options": [option.model_dump() for option in row.options],
                    "multiSelect": row.is_multi_select,
                }
                for index, row in enumerate(spec.questions)
            ]
            answers = await self.callbacks.question(questions)
            return types.QuestionHookResult(
                responses=[
                    types.QuestionResponse(
                        freeform_response=answers.get(str(index), "")
                    )
                    for index, _ in enumerate(spec.questions)
                ]
            )

        servers = [
            types.McpStdioServer(name=name, **row)
            for name, row in mcp_servers(self.settings, self.env).items()
        ]
        options = dict(
            workspaces=[self.settings["cwd"]],
            env=variables,
            conversation_id=self.identity,
            save_dir=str(self.directory / "sessions"),
            app_data_dir=str(self.directory / "app"),
            session_continuation_mode=types.SessionContinuationMode.RESUME
            if self.restore
            else types.SessionContinuationMode.CREATE_ONLY,
            system_instructions=(self.settings.get("instructions") or {}).get("text"),
            skills_paths=[row["export"] for row in self.settings.get("skills", [])],
            mcp_servers=servers,
            policies=[],
            hooks=[
                policy.enforce(
                    [policy.ask_user("*", handler=approval)], mcp_servers=servers
                ),
                interaction,
                tool_finished,
            ],
            model=self.settings.get("model"),
            api_key=api_key,
            triggers=[],
            capabilities=types.CapabilitiesConfig(
                disabled_tools=[
                    types.BuiltinTools.INVOKE_SUBAGENT,
                    types.BuiltinTools.RUN_WORKFLOW,
                    types.BuiltinTools.SCHEDULE,
                ],
                run_command_config=types.RunCommandConfig(enable_daemons=False),
            ),
        )
        if vertex:
            options.update(
                vertex=True,
                project=self.env["GOOGLE_CLOUD_PROJECT"],
                location=self.env.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            )
        if self.env.get("GEMINI_API_BASE_URL"):
            if vertex:
                raise ConversationError(
                    "unsupported",
                    "Gemini API endpoint override does not apply to Google Cloud authentication",
                )
            from google.antigravity.models import DEFAULT_MODEL

            options.pop("model", None)
            options["models"] = [
                types.ModelTarget(
                    name=self.settings.get("model") or DEFAULT_MODEL,
                    endpoint=types.GeminiAPIEndpoint(
                        api_key=api_key, base_url=self.env["GEMINI_API_BASE_URL"]
                    ),
                )
            ]
        self.agent = Agent(LocalAgentConfig(**options))
        await self.agent.__aenter__()
        self.entered = True
        observed = self.agent.conversation_id
        self.identity_observed = observed == self.identity
        return observed or self.identity

    async def prompt(self, text):
        self.cancel_requested = False
        self.started_tools.clear()
        self.finished_tools.clear()
        self.response = await self.agent.chat(text)
        if self.cancel_requested:
            await self.response.cancel()
        try:
            async for chunk in self.response.chunks:
                observed = self.agent.conversation_id
                if observed:
                    if observed != self.identity:
                        raise ConversationError(
                            "uncertain",
                            "Antigravity observed another conversation identity",
                            uncertain=True,
                            recovery={"observed_native_session_id": observed},
                        )
                    self.identity_observed = True
                # Official tool hooks expose execution IDs. Semantic chunks may
                # instead describe the outer lazy-MCP dispatcher, whose step ID
                # does not identify the executed tool. Hooks own tool events.
                if isinstance(chunk, (self.types.Text, self.types.Thought)):
                    role = (
                        "reasoning"
                        if isinstance(chunk, self.types.Thought)
                        else "assistant"
                    )
                    self.callbacks.emit(
                        "message.delta",
                        {
                            "role": "reasoning"
                            if isinstance(chunk, self.types.Thought)
                            else "assistant",
                            "item_id": "sdk-response:" + role,
                            "text": chunk.text,
                        },
                    )
            usage = self.response.usage_metadata
            if usage:
                self.callbacks.emit(
                    "session.usage", {"usage": usage.model_dump(mode="json")}
                )
            reason = self.response.stop_reason.value
            completed = reason == self.types.StopReason.UNSPECIFIED.value
            return {
                "status": "completed" if completed else "failed",
                "final_text": self.agent.conversation.last_response,
                "stop_reason": reason,
                "error": None
                if completed
                else {
                    "code": "provider_limit",
                    "message": "Antigravity stopped: " + reason,
                },
            }
        except self.types.AntigravityCancelledError:
            return {"status": "cancelled", "stop_reason": "cancelled"}
        finally:
            self.response = None

    async def steer(self, text, input_id):
        from google.antigravity.triggers.triggers import TriggerContext
        # Normal conversation.send() drains the active turn. The public trigger
        # channel adds context without resetting its reader or tool bookkeeping.
        await TriggerContext(self.agent.conversation.connection).send(
            "User follow-up for the current task [" + input_id + "]:\n" + text)

    async def cancel(self):
        self.cancel_requested = True
        if self.response:
            await self.response.cancel()

    def start_tool(self, call):
        identifier = call.id or call.step_id
        if not identifier:
            raise ConversationError(
                "transport",
                "Antigravity tool event lacks a correlation ID",
                uncertain=True,
            )
        if identifier not in self.started_tools:
            self.started_tools.add(identifier)
            self.callbacks.emit(
                "tool.start",
                {
                    "tool_call_id": identifier,
                    "name": str(call.name),
                    "status": "pending",
                    "content": call.args,
                },
            )

    def finish_tool(self, result, *, observed=True):
        identifier = result.id or result.step_id
        if not identifier:
            raise ConversationError(
                "transport",
                "Antigravity tool result lacks a correlation ID",
                uncertain=True,
            )
        if identifier not in self.finished_tools:
            self.finished_tools.add(identifier)
            self.callbacks.emit(
                "tool.result",
                {
                    "tool_call_id": identifier,
                    "name": str(result.name),
                    "status": "failed" if result.error else "completed",
                    "content": result.model_dump(mode="json", exclude={"exception"})[
                        "result"
                    ],
                    "evidence": "sdk-tool-result"
                    if observed
                    else "managed-policy-denial",
                },
            )
        if observed:
            self.callbacks.adapter.core.response_observed(
                self.identity + ":" + result.id
            )

    async def configuration(self):
        from .sdk_commands import permission_option

        return {"options": [permission_option(self.settings)]}

    async def set_permissions(self, value):
        self.settings["access"] = {"permission_mode": value}
        return await self.configuration()

    def commands(self):
        from .sdk_commands import catalog

        return catalog("antigravity-sdk", self.settings)

    async def inspect_command(self, name):
        conversation = self.agent.conversation
        if name in ("context", "usage"):
            usage = conversation.total_usage
            result = {
                "source": "sdk-observed",
                "usage": usage.model_dump(mode="json") if usage else None,
                "provider_account_quota": None,
            }
            if name == "context":
                result.update(
                    turn_count=conversation.turn_count,
                    compaction_indices=conversation.compaction_indices,
                )
            return result
        if name == "skills":
            from .sdk_commands import skills

            return skills(self.settings)
        if name == "mcp":
            return {
                "source": "captured-configuration",
                "live_status_available": False,
                "servers": (self.settings.get("mcp") or {}).get("servers", []),
            }
        raise ConversationError("unsupported", "No SDK inspection mapping")

    async def close(self):
        if self.agent and self.entered:
            await self.agent.__aexit__(None, None, None)
            self.entered = False
