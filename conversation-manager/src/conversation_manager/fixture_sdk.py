"""Credential-free SDK driver injected through an explicit worker service factory."""

import asyncio
import json
import os


class Driver:
    def __init__(self, settings, identity, directory, restore, callbacks, env):
        self.settings, self.identity, self.directory = (
            dict(settings),
            identity,
            directory,
        )
        self.restore, self.callbacks, self.env = restore, callbacks, env
        self.cancelled = asyncio.Event()
        self.path = directory / "fixture-state.json"
        self.protocol = "claude-agent-sdk"

    async def start(self):
        if self.restore:
            self.state = json.loads(self.path.read_text())
            if self.state["id"] != self.identity:
                raise ValueError("Different fixture session")
        else:
            self.state = {"id": self.identity, "prompts": [], "pid": os.getpid()}
            self.save()
        return self.identity

    def save(self):
        self.path.write_text(json.dumps(self.state))

    async def prompt(self, text, command=None):
        self.cancelled.clear()
        self.state["prompts"].append(text)
        self.save()
        if text == 'known-sdk-rejection':
            from .models import ConversationError
            raise ConversationError('provider_rejected', 'Fixture observed a provider rejection')
        if (
            text == "permission"
            and (self.settings.get("access") or {}).get("permission_mode") == "deny"
        ):
            text = "denied"
        self.callbacks.emit(
            "message.delta",
            {"role": "assistant", "item_id": "response", "text": "begin:"},
        )
        if text == 'tool-queue-demo':
            async def wait_tool(seconds):
                try:
                    await asyncio.wait_for(self.cancelled.wait(), seconds)
                    return True
                except TimeoutError:
                    return False
            for tool in ('tool-one', 'tool-two'):
                self.callbacks.emit('tool.start', {'tool_call_id': tool, 'status': 'running'})
            if await wait_tool(2):
                return {'status': 'cancelled', 'stop_reason': 'cancelled'}
            self.callbacks.emit('tool.result', {'tool_call_id': 'tool-one', 'status': 'completed'})
            self.callbacks.emit('tool.start', {'tool_call_id': 'later-tool', 'status': 'running'})
            if await wait_tool(1):
                return {'status': 'cancelled', 'stop_reason': 'cancelled'}
            self.callbacks.emit('tool.result', {'tool_call_id': 'tool-two', 'status': 'completed'})
            if await wait_tool(2):
                return {'status': 'cancelled', 'stop_reason': 'cancelled'}
            self.callbacks.emit('tool.result', {'tool_call_id': 'later-tool', 'status': 'completed'})
            if await wait_tool(.2):
                return {'status': 'cancelled', 'stop_reason': 'cancelled'}
        if text in ("permission", "question"):
            pending = asyncio.create_task(
                self.callbacks.permission("write_file", {"path": "file.txt"}, "call")
                if text == "permission"
                else self.callbacks.question(
                    [{"id": "choice", "question": "Choose a name"}]
                )
            )
            cancellation = asyncio.create_task(self.cancelled.wait())
            await asyncio.wait(
                (pending, cancellation), return_when=asyncio.FIRST_COMPLETED
            )
            if self.cancelled.is_set():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                return {"status": "cancelled", "stop_reason": "cancelled"}
            response = await pending
            cancellation.cancel()
            await asyncio.gather(cancellation, return_exceptions=True)
            text = (
                "approved"
                if response is True
                else "denied"
                if response is False
                else response["choice"]
            )
            self.callbacks.emit(
                "tool.result",
                {"tool_call_id": "call", "status": "completed", "content": text},
            )
        if text.startswith("hold") or ('This is a workflow task.' in text and '\n\nhold' in text):
            await self.cancelled.wait()
            return {"status": "cancelled", "stop_reason": "cancelled"}
        if text == "queue-demo":
            await asyncio.sleep(3)
        if text == "slow":
            await asyncio.sleep(0.2)
        if text == "secret":
            text = self.env.get("ANTHROPIC_API_KEY", self.env.get("GEMINI_API_KEY", ""))
        if text == "recall":
            text = self.state["prompts"][0]
        if text == "limit":
            return {"status": "failed", "stop_reason": "MAX_TOTAL_TOKENS_EXCEEDED"}
        if 'This is a workflow task.' in text or 'This is a workflow task.' in (self.settings.get('instructions') or {}).get('text', ''):
            text = json.dumps({'outcome': 'completed', 'report': 'SDK fixture report: ' + text[:200]})
        self.callbacks.emit(
            "message.delta", {"role": "assistant", "item_id": "response", "text": text}
        )
        if command:
            if text.startswith("/compact"):
                self.callbacks.emit(
                    "session.compacted",
                    {
                        "metadata": {"trigger": "manual", "pre_tokens": 100},
                        "source": "sdk-observed",
                    },
                )
            self.callbacks.emit(
                "command.output", {**command, "text": text, "format": "text"}
            )
        return {"status": "completed", "stop_reason": "completed", "final_text": text}

    def commands(self):
        from .adapters.sdk_commands import catalog

        return catalog(
            self.protocol,
            self.settings,
            [
                {"name": "compact"},
                {"name": "clear"},
                {"name": "model"},
                {"name": "effort"},
                {"name": "loop"},
                *[
                    {"name": "manager-resources:" + row["name"]}
                    for row in self.settings.get("skills", [])
                ],
            ],
        )

    async def inspect_command(self, name):
        if name == "skills":
            from .adapters.sdk_commands import skills

            return skills(self.settings)
        return {
            "source": "sdk-observed" if name != "mcp" else "captured-configuration",
            "turn_count": len(self.state["prompts"]),
            "prompts": list(self.state["prompts"]),
        }

    async def steer(self, text, input_id):
        self.state.setdefault("steering", []).append({"id": input_id, "text": text})
        self.save()

    async def cancel(self):
        self.cancelled.set()

    async def configuration(self):
        from .adapters.sdk_commands import permission_option

        options = (
            [
                {
                    "id": "model",
                    "category": "model",
                    "name": "Model",
                    "type": "select",
                    "currentValue": self.settings.get("model", "fixture-one"),
                    "options": [
                        {"value": "fixture-one", "name": "One"},
                        {"value": "fixture-two", "name": "Two"},
                    ],
                }
            ]
            if self.protocol in ("claude-agent-sdk", "copilot-sdk")
            else []
        )
        if self.protocol == "claude-agent-sdk":
            options.append(
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
                }
            )
        return {"options": options + [permission_option(self.settings)]}

    async def set_model(self, model):
        if self.protocol == 'copilot-sdk' and model == 'fixture-confirmed':
            await self.callbacks.question([{'id': 'model-compaction', 'question': 'Compact before switching?',
                'options': [{'label': 'Compact history and switch'}, {'label': 'Keep current model'}]}], manager_owned=True)
        self.settings["model"] = model
        return await self.configuration()

    async def set_mode(self, value):
        self.settings["mode"] = value
        return await self.configuration()

    async def set_permissions(self, value):
        self.settings["access"] = {"permission_mode": value}
        return await self.configuration()

    async def close(self):
        self.state["closed"] = True
        self.save()


class Services:
    def sdk_driver(
        self, protocol, settings, identity, directory, restore, callbacks, env
    ):
        driver = Driver(settings, identity, directory, restore, callbacks, env)
        driver.protocol = protocol
        return driver


def factory(options):
    return Services()
