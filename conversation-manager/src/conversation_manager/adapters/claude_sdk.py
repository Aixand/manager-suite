"""Claude Agent SDK driver with explicitly selected API or profile authentication."""

import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

from ..models import ConversationError
from .sdk_resources import claude_plugin, mcp_servers


def terminal_error(message, method='profile-login'):
    """Classify provider failures without exposing raw SDK diagnostics or tokens."""
    if not getattr(message, 'is_error', False):
        return None
    values = [getattr(message, 'result', ''), getattr(message, 'terminal_reason', '')]
    errors = getattr(message, 'errors', [])
    if isinstance(errors, list):
        values.extend(item for item in errors if isinstance(item, str))
    text = ' '.join(value for value in values if isinstance(value, str)).lower()
    if any(marker in text for marker in ('not logged in', 'please run /login', 'login expired',
            'oauth token expired', 'authentication_failed', 'authentication_error', 'invalid api key')):
        action = 'Sign in to the original account' if method == 'profile-login' else 'Update the selected API credential'
        return {'code': 'authentication', 'message': f'Claude authentication needs attention. {action} in Agents → Manage profile, then continue this chat. The request was not retried.'}
    return {'code': 'provider', 'message': 'Claude ended without successful completion'}


def environment(values, directory, authentication=None):
    # The SDK merges os.environ with its env option. Explicitly clear inherited
    # entries before adding the caller's captured environment.
    result = {key: "" for key in os.environ}
    # Windows process creation and system utilities need these non-secret OS paths.
    for key in ('SystemRoot', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP'):
        if key in os.environ:
            result[key] = os.environ[key]
    result.update(values)
    result.update(
        HOME=str(directory),
        CLAUDE_CONFIG_DIR=str(Path(directory) / "claude"),
        CLAUDE_CODE_OAUTH_TOKEN="",
        ANTHROPIC_AUTH_TOKEN="",
        CLAUDE_CODE_USE_BEDROCK="0",
        CLAUDE_CODE_USE_VERTEX="0",
        CLAUDE_CODE_USE_FOUNDRY="0",
        CLAUDE_CODE_USE_ANTHROPIC_AWS="0",
    )
    if (authentication or {}).get("method") == "profile-login":
        home, config = values.get("HOME", ""), values.get("CLAUDE_CONFIG_DIR")
        if not home or not Path(home).is_absolute() or config is None or config and not Path(config).is_absolute():
            raise ConversationError("authentication", "Claude profile login requires its explicitly selected configuration directory")
        result.update(HOME=home, CLAUDE_CONFIG_DIR=config)
        if config == "":
            if "CLAUDE_CONFIG_DIR" in os.environ:
                raise ConversationError("authentication", "Native Claude login requires a managed worker with its inherited configuration directory cleared")
            result.pop("CLAUDE_CONFIG_DIR", None)
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_CUSTOM_HEADERS",
                    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "CLAUDE_CODE_OAUTH_SCOPES",
                    "ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID"):
            result[key] = ""
    if os.name == 'nt':
        result['USERPROFILE'] = result['HOME']
        result['APPDATA'] = str(Path(result['HOME']) / 'AppData/Roaming')
        result['LOCALAPPDATA'] = str(Path(result['HOME']) / 'AppData/Local')
    if sys.platform == 'darwin':
        # Clearing inherited secrets also clears USER. Claude's Keychain lookup
        # requires this OS identity in addition to the selected config directory.
        import pwd
        username = pwd.getpwuid(os.getuid()).pw_name
        for key in ('USER', 'LOGNAME'):
            if not result.get(key):
                result[key] = username
    return result


class ClaudeDriver:
    def __init__(self, settings, identity, directory, restore, callbacks, env, *, authentication=None):
        self.settings, self.identity, self.directory, self.restore = (
            settings,
            identity,
            directory,
            restore,
        )
        self.authentication = authentication or {"method": "api-key"}
        self.callbacks, self.env = callbacks, environment(env, directory, self.authentication)
        self.client = None
        self.cancel_requested = False
        self.models = []
        self.model = settings.get("model")
        self.tool_names = {}
        self.identity_observed = False
        self.command_metadata = []
        self.terminal_commands = []
        self.last_usage = {}
        self.estimated_cost = None

    async def start(self):
        from ..sdk_runtime import activate
        activate(self.env.get('AIXAND_PROVIDER_SDK_PATH'))
        import claude_agent_sdk as sdk

        self.sdk = sdk
        cli = self.env.get("AIXAND_CLAUDE_CLI", "")
        if not cli or not Path(cli).is_absolute() or not Path(cli).is_file():
            raise ConversationError("installation", "Select an installed official Claude Code executable; the SDK cannot use an embedded fallback")
        from ..provider_policy import inspect_cli
        checked = await asyncio.to_thread(inspect_cli, 'claude', [cli], env=self.env, backend='claude-sdk')
        if not checked['compatible']:
            raise ConversationError('unsupported', checked['reason'])
        if self.authentication.get("method") != "profile-login" and not self.env.get("ANTHROPIC_API_KEY"):
            raise ConversationError(
                "authentication", "Claude SDK requires an explicitly selected API key"
            )
        config = Path(self.env["CLAUDE_CONFIG_DIR"]) if self.env.get("CLAUDE_CONFIG_DIR") else Path(self.env["HOME"]) / ".claude"
        config.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.restore:
            transcripts = list(config.glob("projects/**/*.jsonl"))
            if not any(
                path.stem == self.identity and not path.is_symlink()
                for path in transcripts
            ):
                raise ConversationError(
                    "not_found",
                    "Claude transcript is missing; restore cannot create a replacement",
                )
        plugins, skills = claude_plugin(self.settings, self.directory)

        async def permission(tool, arguments, context):
            if tool == "AskUserQuestion":
                questions = arguments.get("questions", [])
                supplied = await self.callbacks.question(questions)
                answers = {
                    row["question"]: supplied[row.get("id") or str(index)]
                    for index, row in enumerate(questions)
                }
                return sdk.PermissionResultAllow(
                    updated_input={**arguments, "answers": answers}
                )
            if (self.settings.get("access") or {}).get(
                "permission_mode", "ask"
            ) == "deny":
                return sdk.PermissionResultDeny(
                    message="Permission prompts are denied by the captured policy"
                )
            allowed = await self.callbacks.permission(
                tool, arguments, getattr(context, "tool_use_id", None)
            )
            return (
                sdk.PermissionResultAllow(updated_input=arguments)
                if allowed
                else sdk.PermissionResultDeny(message="User denied this tool call")
            )

        async def foreground_only(data, tool_id, context):
            if data.get("tool_input", {}).get("run_in_background") or data.get(
                "tool_input", {}
            ).get("background"):
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Background execution is not qualified for this retained SDK session",
                    }
                }
            return {}

        options = dict(
            cli_path=cli,
            cwd=self.settings["cwd"],
            env=self.env,
            model=self.model,
            tools={"type": "preset", "preset": "claude_code"},
            allowed_tools=[],
            permission_mode=self.settings.get("mode", "default"),
            setting_sources=[],
            skills=skills,
            plugins=plugins,
            mcp_servers=mcp_servers(self.settings, self.env),
            include_partial_messages=True,
            can_use_tool=permission,
            hooks={"PreToolUse": [sdk.HookMatcher(hooks=[foreground_only])]},
        )
        options["extra_args"] = {"strict-mcp-config": None}
        if getattr(self, "discovery", False):
            options["extra_args"]["no-session-persistence"] = None
        text = (self.settings.get("instructions") or {}).get("text")
        if text:
            options["system_prompt"] = {
                "type": "preset",
                "preset": "claude_code",
                "append": text,
            }
        if self.restore:
            options["resume"] = self.identity
        else:
            options["session_id"] = self.identity
        self.client = sdk.ClaudeSDKClient(sdk.ClaudeAgentOptions(**options))
        await self.client.connect()
        info = await self.client.get_server_info() or {}
        observed = info.get("session_id", info.get("sessionId"))
        self.identity_observed = observed == self.identity
        if observed and observed != self.identity:
            raise ConversationError(
                "uncertain",
                "Claude initialization changed the requested session ID",
                uncertain=True,
            )
        self.models = info.get("models", [])
        self.command_metadata = info.get("commands", [])
        return self.identity

    def commands(self):
        from .sdk_commands import catalog

        return catalog(
            "claude-agent-sdk",
            self.settings,
            self.command_metadata,
            self.terminal_commands,
        )

    async def inspect_command(self, name):
        if name == "context":
            return await self.client.get_context_usage()
        if name == "mcp":
            return await self.client.get_mcp_status()
        if name == "skills":
            from .sdk_commands import skills

            return skills(self.settings)
        if name == "usage":
            return {
                "source": "sdk-observed",
                "last_turn": self.last_usage,
                "usage_scope": "last-turn-main-agent",
                "estimated_cost_usd": self.estimated_cost,
                "cost_scope": "sdk-session-cumulative",
                "provider_account_quota": None,
            }
        raise ConversationError("unsupported", "No SDK inspection mapping")

    async def prompt(self, text, command=None):
        self.cancel_requested = False
        self.tool_names = {}
        streamed, started, block_types = set(), set(), {}
        current_message = "assistant"
        assistant_text = []
        output_length, output_truncated = 0, False
        output_limit = (
            250000  # Fits bounded IPC/history pages even after JSON escaping.
        )
        compaction = None

        def capture_text(value):
            nonlocal output_length, output_truncated
            remaining = output_limit - output_length
            if remaining:
                assistant_text.append(value[:remaining])
                output_length += min(len(value), remaining)
            output_truncated |= len(value) > remaining

        await self.client.query(text)
        async for message in self.client.receive_response():
            name = type(message).__name__
            observed = getattr(message, "session_id", None)
            if observed and observed != self.identity:
                raise ConversationError(
                    "uncertain",
                    "Claude event belongs to a different session",
                    uncertain=True,
                )
            if observed == self.identity:
                self.identity_observed = True
            if name == "StreamEvent":
                event = message.event
                kind = event.get("type")
                if kind == "message_start":
                    current_message = event.get("message", {}).get(
                        "id", current_message
                    )
                elif kind == "content_block_start":
                    index, block = event.get("index", 0), event.get("content_block", {})
                    block_types[index] = block.get("type")
                    if block.get("type") == "tool_use":
                        identifier = block["id"]
                        started.add(identifier)
                        self.tool_names[identifier] = block.get("name", "tool")
                        self.callbacks.emit(
                            "tool.start",
                            {
                                "tool_call_id": identifier,
                                "name": self.tool_names[identifier],
                                "status": "pending",
                                "content": block.get("input", {}),
                            },
                        )
                elif kind == "content_block_delta":
                    delta, index = event.get("delta", {}), event.get("index", 0)
                    value = delta.get("text", delta.get("thinking"))
                    if value:
                        role = (
                            "reasoning"
                            if delta.get("type") == "thinking_delta"
                            else "assistant"
                        )
                        streamed.add((current_message, index, role))
                        if role == "assistant":
                            capture_text(value)
                        self.callbacks.emit(
                            "message.delta",
                            {
                                "role": role,
                                "item_id": current_message + ":" + str(index),
                                "text": value,
                            },
                        )
            elif name == "AssistantMessage":
                identifier = getattr(message, "message_id", None) or current_message
                for index, block in enumerate(message.content):
                    block_name = type(block).__name__
                    if block_name in ("TextBlock", "ThinkingBlock"):
                        role = "assistant" if block_name == "TextBlock" else "reasoning"
                        if (identifier, index, role) not in streamed and not (
                            current_message,
                            index,
                            role,
                        ) in streamed:
                            value = getattr(
                                block, "text", getattr(block, "thinking", "")
                            )
                            if role == "assistant":
                                capture_text(value)
                            self.callbacks.emit(
                                "message.delta",
                                {
                                    "role": role,
                                    "item_id": identifier + ":" + str(index),
                                    "text": getattr(
                                        block, "text", getattr(block, "thinking", "")
                                    ),
                                },
                            )
                    elif block_name == "ToolUseBlock":
                        self.tool_names[block.id] = block.name
                        if block.id not in started:
                            started.add(block.id)
                            self.callbacks.emit(
                                "tool.start",
                                {
                                    "tool_call_id": block.id,
                                    "name": block.name,
                                    "status": "pending",
                                    "content": block.input,
                                },
                            )
            elif name == "UserMessage" and isinstance(message.content, list):
                for block in message.content:
                    if type(block).__name__ == "ToolResultBlock":
                        self.callbacks.emit(
                            "tool.result",
                            {
                                "tool_call_id": block.tool_use_id,
                                "name": self.tool_names.get(block.tool_use_id, "tool"),
                                "status": "failed" if block.is_error else "completed",
                                "content": block.content,
                            },
                        )
                        self.callbacks.adapter.core.response_observed(
                            self.identity + ":" + block.tool_use_id
                        )
            elif isinstance(message, self.sdk.SystemMessage):
                data = getattr(message, "data", {})
                if getattr(message, "subtype", None) == "init":
                    if data.get("session_id", self.identity) != self.identity:
                        raise ConversationError(
                            "uncertain",
                            "Claude initialized another session",
                            uncertain=True,
                        )
                    if data.get("plugin_errors"):
                        raise ConversationError(
                            "integrity", "Captured Claude plugin did not load"
                        )
                    if data.get("session_id") == self.identity:
                        self.identity_observed = True
                    if "slash_commands" in data:
                        old = {
                            row["name"]: row
                            for row in self.command_metadata
                            if isinstance(row, dict) and "name" in row
                        }
                        self.command_metadata = [
                            old.get(value, {"name": value})
                            for value in data["slash_commands"]
                        ]
                        self.terminal_commands = data.get("terminal_slash_commands", [])
                        self.callbacks.emit("session.commands", self.commands())
                elif message.subtype == "compact_boundary":
                    compaction = data.get("compact_metadata", {})
                    self.callbacks.emit(
                        "session.compacted",
                        {
                            "metadata": compaction,
                            "source": "sdk-observed",
                        },
                    )
                elif message.subtype == "local_command_result" and isinstance(
                    data.get("content"), str
                ):
                    capture_text(data["content"])
                    self.callbacks.emit(
                        "message.delta",
                        {
                            "role": "assistant",
                            "item_id": "sdk-command-result",
                            "text": data["content"],
                        },
                    )
            elif name == "ResultMessage":
                self.last_usage = message.usage or {}
                # Streaming client results carry a running SDK estimate. Adding
                # them would count earlier turns repeatedly, including on resume.
                self.estimated_cost = getattr(message, "total_cost_usd", None)
                result_text = getattr(message, "result", None)
                if not assistant_text and isinstance(result_text, str) and result_text:
                    capture_text(result_text)
                    self.callbacks.emit(
                        "message.delta",
                        {
                            "role": "assistant",
                            "item_id": "sdk-result",
                            "text": result_text,
                        },
                    )
                if command:
                    output = "".join(assistant_text)
                    if not output:
                        # Some SDK local commands return no assistant text. Report
                        # their observed terminal result instead of a blank dialog.
                        observed = {
                            "subtype": message.subtype,
                            "is_error": message.is_error,
                            "terminal_reason": getattr(
                                message, "terminal_reason", None
                            ),
                        }
                        if compaction is not None:
                            observed["compaction"] = compaction
                        output = json.dumps(observed, indent=2, ensure_ascii=False)
                    self.callbacks.emit(
                        "command.output",
                        {
                            **command,
                            "text": output[:output_limit],
                            "format": "text",
                            "truncated": output_truncated or len(output) > output_limit,
                            "source": "sdk-observed",
                        },
                    )
                self.callbacks.emit(
                    "session.usage",
                    {
                        "usage": message.usage,
                        "cost_usd": getattr(message, "total_cost_usd", None),
                    },
                )
                reason = getattr(message, "terminal_reason", None)
                if self.cancel_requested and reason in (
                    "aborted_streaming",
                    "aborted_tools",
                ):
                    return {"status": "cancelled", "stop_reason": "cancelled"}
                completed = (
                    not message.is_error
                    and message.subtype == "success"
                    and reason in (None, "completed")
                )
                return {
                    "status": "completed" if completed else "failed",
                    "final_text": result_text if isinstance(result_text, str) else "".join(assistant_text),
                    "stop_reason": reason or message.subtype,
                    "error": None
                    if completed
                    else terminal_error(message, self.authentication.get('method')) or {"code": "provider", "message": "Claude ended without successful completion"},
                }
        raise ConversationError(
            "transport",
            "Claude response ended without a terminal result",
            uncertain=True,
        )

    async def steer(self, text, input_id):
        async def message():
            yield {"type": "user", "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, input_id)), "session_id": self.identity,
                   "message": {"role": "user", "content": text}, "parent_tool_use_id": None}
        await self.client.query(message(), session_id=self.identity)

    async def cancel(self):
        self.cancel_requested = True
        await self.client.interrupt()

    async def configuration(self):
        from .sdk_commands import permission_option

        choices = [
            {
                "value": row.get("value", row.get("id")),
                "name": row.get(
                    "displayName", row.get("name", row.get("value", row.get("id")))
                ),
            }
            for row in self.models
            if isinstance(row, dict) and row.get("value", row.get("id"))
        ]
        if self.model and not any(row["value"] == self.model for row in choices):
            choices.append({"value": self.model, "name": self.model})
        model_options = (
            [
                {
                    "id": "model",
                    "category": "model",
                    "name": "Model",
                    "type": "select",
                    "currentValue": self.model or "",
                    "options": choices,
                }
            ]
            if choices
            else []
        )
        return {
            "options": model_options
            + [
                {
                    "id": "mode",
                    "category": "mode",
                    "name": "Agent mode",
                    "type": "select",
                    "currentValue": self.settings.get("mode", "default"),
                    "options": [
                        {"value": "default", "name": "Default"},
                        {"value": "plan", "name": "Plan"},
                    ],
                },
                permission_option(self.settings),
            ]
        }

    async def set_mode(self, value):
        await self.client.set_permission_mode(value)
        self.settings["mode"] = value
        return await self.configuration()

    async def set_permissions(self, value):
        self.settings["access"] = {"permission_mode": value}
        return await self.configuration()

    async def set_model(self, value):
        await self.client.set_model(value)
        self.model = value
        return await self.configuration()

    async def close(self):
        if self.client:
            await self.client.disconnect()
