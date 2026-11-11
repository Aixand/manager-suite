"""Official Copilot SDK behind retained session, approval and operation ownership."""
import asyncio
import json
from pathlib import Path

from ..models import ConversationError
from ..turn_output import TurnOutput
from .sdk_resources import mcp_servers


def record(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, 'to_dict'):
        return value.to_dict()
    return vars(value)


class CopilotDriver:
    def __init__(self, settings, identity, directory, restore, callbacks, env, *, authentication=None):
        self.settings, self.identity, self.directory, self.restore = dict(settings), identity, directory, restore
        self.callbacks, self.env = callbacks, dict(env)
        self.authentication = authentication or {}
        self.client, self.session, self.unsubscribe = None, None, None
        self.identity_observed = False
        self.models, self.model, self.account = [], None, None
        self.expected_account = None
        self.events = None
        self.cancel_requested = False
        self.last_usage = {}
        self.configuration_uncertain = False

    async def call(self, awaitable):
        try:
            return await awaitable
        except ConversationError:
            raise
        except Exception as error:
            from copilot._jsonrpc import JsonRpcError
            if isinstance(error, JsonRpcError):
                auth = any(word in str(error).lower() for word in ('not authenticated', 'unauthorized', 'authentication'))
                raise ConversationError('authentication' if auth else 'provider_rejected',
                    'Copilot authentication needs attention' if auth else 'Copilot rejected this operation') from error
            raise ConversationError('transport', 'Copilot delivery is unconfirmed; inspect the retained session', uncertain=True) from error

    async def connect(self):
        from ..sdk_runtime import activate
        activate(self.env.get('AIXAND_PROVIDER_SDK_PATH'))
        from copilot import CopilotClient, RuntimeConnection

        executable = self.env.get('AIXAND_COPILOT_CLI', '')
        if not Path(executable).is_absolute() or not Path(executable).is_file():
            raise ConversationError('installation', 'Select an installed Copilot CLI; SDK runtime downloads are disabled')
        variables = dict(self.env, COPILOT_SKIP_CLI_DOWNLOAD='1')
        token_variable = variables.pop('AIXAND_COPILOT_TOKEN_VARIABLE', '')
        token = variables.get(token_variable) if self.authentication.get('method') == 'copilot-token' else None
        # Credentials enter only through the explicitly captured route.
        for key in ('COPILOT_GITHUB_TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN', 'COPILOT_SDK_AUTH_TOKEN'):
            variables.pop(key, None)
        self.client = CopilotClient(connection=RuntimeConnection.for_stdio(path=executable,
            args=json.loads(variables.get('AIXAND_COPILOT_ARGS', '[]'))),
            env=variables, working_directory=self.settings['cwd'],
            github_token=token, use_logged_in_user=self.authentication.get('method') == 'copilot-login',
            base_directory=variables.get('COPILOT_HOME'))
        await self.call(self.client.start())
        status = record(await self.call(self.client.get_status()))
        from ..provider_policy import compatibility
        checked = compatibility('copilot', status.get('version', '').rstrip('.'), backend='copilot-sdk')
        if not checked['compatible']:
            raise ConversationError('unsupported', checked['reason'])
        await self.check_account()
        await self.catalog()

    async def check_account(self):
        auth = record(await self.call(self.client.get_auth_status()))
        if self.authentication.get('method') != 'copilot-byok' and not auth.get('isAuthenticated'):
            raise ConversationError('authentication', 'Sign in to the selected Copilot profile before continuing')
        current = {key: auth.get(key) for key in ('authType', 'host', 'login')}
        if (self.account is not None and current != self.account) or (self.expected_account is not None and current != self.expected_account):
            raise ConversationError('conflict', 'The selected Copilot account differs from the saved session')
        self.account = current

    async def catalog(self):
        if self.authentication.get("method") == "copilot-byok":
            model = self.env.get("COPILOT_MODEL")
            self.models = [dict(id=model, name=model)] if model else []
            return self.models
        rows = await self.call(self.client.list_models())
        self.models = [record(row) for row in rows]
        return self.models

    def validate_model(self, value):
        selected = next((row for row in self.models if row['id'] == value), None)
        if selected is None:
            # BYOK supplies an explicit configured model instead of an account catalog.
            if self.authentication.get('method') == 'copilot-byok' and value == self.env.get('COPILOT_MODEL'):
                return
            raise ConversationError('validation', 'Choose a model advertised for the selected Copilot account')
        policy = selected.get('policy') or {}
        if policy.get('state') in ('disabled', 'unconfigured'):
            raise ConversationError('validation', 'This Copilot model is unavailable under the account policy')

    async def start(self):
        await self.connect()
        if getattr(self, 'discovery', False):
            return self.identity
        requested = self.settings.get('model')
        if requested:
            self.validate_model(requested)
        if self.restore and self.authentication.get('method') == 'copilot-login' and not (self.expected_account or {}).get('login'):
            raise ConversationError('authentication', 'The original Copilot account identity could not be confirmed for restoration')
        if self.restore:
            # resume_session may otherwise synthesize a missing session; require its saved identity.
            sessions = await self.call(self.client.list_sessions())
            if not any(record(row).get('sessionId', record(row).get('session_id')) == self.identity for row in sessions):
                raise ConversationError('not_found', 'The original Copilot session is unavailable; a replacement was not created')
        options = dict(on_permission_request=self.permission, on_user_input_request=self.question,
            working_directory=self.settings['cwd'], streaming=True,
            mcp_servers={name: dict(row, type='local', tools=['*']) for name, row in mcp_servers(self.settings, self.env).items()},
            skill_directories=self.skill_directories(),
            config_directory=self.env.get('COPILOT_HOME'), enable_file_hooks=False, enable_config_discovery=False)
        if self.authentication.get('method') == 'copilot-byok':
            options['provider'] = dict(type=self.env.get('COPILOT_PROVIDER_TYPE', 'openai'),
                base_url=self.env['COPILOT_PROVIDER_BASE_URL'], api_key=self.env.get('COPILOT_PROVIDER_API_KEY', ''),
                wire_api=self.env.get('COPILOT_PROVIDER_WIRE_API', 'completions'))
        if requested:
            options['model'] = requested
        instructions = (self.settings.get('instructions') or {}).get('text')
        if instructions:
            options['system_message'] = {'mode': 'append', 'content': instructions}
        if self.restore:
            self.session = await self.call(self.client.resume_session(self.identity, **options))
        else:
            self.session = await self.call(self.client.create_session(session_id=self.identity, **options))
        if self.session.session_id != self.identity:
            raise ConversationError('uncertain', 'Copilot returned a different session identity', uncertain=True,
                              recovery={'observed_native_session_id': self.session.session_id})
        self.identity_observed = True
        self.unsubscribe = self.session.on(self.observe)
        await self.current_model()
        if requested and self.model != requested:
            raise ConversationError('configuration', 'Copilot did not confirm the requested model', uncertain=True,
                              recovery={'native_session_id': self.identity})
        self.settings['model'] = self.model
        return self.identity

    def skill_directories(self):
        import shutil
        from ..skill_delivery import verify_export
        root = self.directory / 'skills'
        for row in self.settings.get('skills', []):
            target = root / row['name']
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copytree(row['export'], target)
            verify_export({**row, 'export': str(target)})
        return [str(root)] if self.settings.get('skills') else []

    async def current_model(self):
        # A newly created session may defer its provider default until the first
        # prompt. This is distinct from missing readback after an explicit switch.
        allow_default = not self.settings.get('model') and not self.model and not self.configuration_uncertain
        self.configuration_uncertain = True
        if self.authentication.get("method") != "copilot-byok":
            await self.call(self.session.rpc.model.list())
        value = record(await self.call(self.session.rpc.model.get_current()))
        if value.get('ready') is False:
            raise ConversationError('configuration', 'Copilot model state is not ready', uncertain=True)
        model = value.get('modelId', value.get('model_id'))
        if not model:
            model = (value.get('model') or value.get('modelState') or {}).get('modelId')
        if not model:
            if allow_default:
                self.configuration_uncertain = False
                return None
            raise ConversationError('configuration', 'Copilot did not report the active model', uncertain=True)
        self.model = model
        self.configuration_uncertain = False
        return model

    async def configuration(self):
        from .sdk_commands import permission_option
        if self.session:
            await self.check_account()
            await self.current_model()
        choices = [dict(value=row['id'], name=row.get('name', row['id'])) for row in self.models
                   if (row.get('policy') or {}).get('state') not in ('disabled', 'unconfigured')]
        if self.model and not any(row['value'] == self.model for row in choices):
            choices.append(dict(value=self.model, name=self.model))
        return {'options': [dict(id='model', category='model', name='Model', type='select',
            currentValue=self.model or '', defaultUnknown=not bool(self.model), options=choices), permission_option(self.settings)]}

    async def set_model(self, value):
        from copilot.generated.rpc import ModelSwitchToRequest
        await self.check_account()
        await self.catalog()
        self.validate_model(value)
        self.configuration_uncertain = True
        async def switch(decision=None):
            try:
                return record(await self.call(self.session.rpc.model.switch_to(ModelSwitchToRequest(
                    model_id=value, model_change_scope='session', require_available=True,
                    run_compaction_preflight=True, compaction_decision=decision))))
            except ConversationError as error:
                if not error.uncertain:
                    self.configuration_uncertain = False
                raise

        result = await switch()
        declined = False
        if result.get('confirmation') or result.get('status') == 'confirmation_required':
            details = result.get('confirmation') or {}
            answers = await self.callbacks.question([dict(id='model-compaction', header='Change model',
                question=f"Switch to {details.get('targetModelDisplayName', value)}? Copilot needs to compact the conversation history first.",
                options=[{'label': 'Compact history and switch'}, {'label': 'Keep current model'}])], manager_owned=True)
            await self.check_account()
            decision = 'compact' if answers.get('model-compaction') == 'Compact history and switch' else 'cancel'
            result = await switch(decision)
            declined = decision == 'cancel'
            if result.get('confirmation') or result.get('status') == 'confirmation_required':
                raise ConversationError('configuration', 'Copilot has not confirmed the model change', uncertain=True)
        if result.get('deferred') or result.get('persistenceError'):
            raise ConversationError('configuration', 'Copilot model change is not durably confirmed', uncertain=True,
                recovery={'native_session_id': self.identity, 'queue_id': result.get('queueId')})
        if declined:
            await self.current_model()
            raise ConversationError('cancelled', 'Copilot model change was cancelled')
        if result.get('status') in ('failed', 'rejected', 'cancelled', 'canceled', 'unavailable'):
            await self.current_model()
            raise ConversationError('provider_rejected', 'Copilot did not apply the selected model')
        await self.current_model()
        if self.model != value:
            self.configuration_uncertain = True
            raise ConversationError('configuration', 'Copilot did not confirm the selected model', uncertain=True)
        self.settings['model'] = self.model
        return await self.configuration()

    async def set_permissions(self, value):
        self.settings['access'] = {'permission_mode': value}
        return await self.configuration()

    async def permission(self, request, invocation):
        from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionDeniedInteractivelyByUser
        raw = record(request)
        allowed = False
        if (self.settings.get('access') or {}).get('permission_mode', 'ask') != 'deny':
            allowed = await self.callbacks.permission(str(raw.get('kind', 'tool')), raw,
                raw.get('toolCallId', raw.get('tool_call_id')))
        return PermissionDecisionApproveOnce(approved_interactively=True) if allowed else PermissionDecisionDeniedInteractivelyByUser()

    async def question(self, request, context):
        answers = await self.callbacks.question([{'id': '0', 'question': request.get('question', ''),
            'options': [{'label': value} for value in request.get('choices', [])]}])
        return {'answer': answers['0'], 'wasFreeform': answers['0'] not in request.get('choices', [])}

    def observe(self, event):
        # Subagent completions never complete the retained root turn.
        if getattr(event, 'agent_id', None):
            return
        if self.events is not None:
            self.events.put_nowait(event)

    async def prompt(self, text):
        if self.configuration_uncertain:
            raise ConversationError('configuration', 'Inspect and confirm the Copilot model before sending')
        await self.check_account()
        self.events, self.cancel_requested = asyncio.Queue(), False
        output, aborted, failed = TurnOutput(), False, False
        # Let the official SDK correlate root completion, including subagent and
        # background activity. Streaming observation never declares completion.
        completion = asyncio.create_task(self.session.send_and_wait(text, timeout=3600))
        waiting = None
        try:
            while True:
                if waiting is None:
                    waiting = asyncio.create_task(self.events.get())
                await asyncio.wait((completion, waiting), return_when=asyncio.FIRST_COMPLETED)
                if waiting.done():
                    event = waiting.result()
                    waiting = None
                    kind = getattr(event.type, 'value', event.type)
                    data = record(event.data)
                    if kind in ('assistant.message_delta', 'assistant.reasoning_delta'):
                        chunk = data.get('deltaContent', '')
                        role = 'reasoning' if kind == 'assistant.reasoning_delta' else 'assistant'
                        if role == 'assistant':
                            output.append(chunk, data.get('messageId'))
                        self.callbacks.emit('message.delta', dict(role=role, text=chunk, item_id=data.get('messageId')))
                    elif kind == 'assistant.message':
                        output.replace(data.get('content', ''), data.get('messageId'))
                    elif kind in ('tool.execution_start', 'tool.execution_complete'):
                        output.tool()
                        self.callbacks.emit('tool.start' if kind.endswith('start') else 'tool.result', dict(
                            tool_call_id=data.get('toolCallId'), name=data.get('toolName'),
                            status='pending' if kind.endswith('start') else 'completed' if data.get('success') else 'failed', content=data))
                    elif kind == 'assistant.usage':
                        self.last_usage = data
                        self.callbacks.emit('session.usage', {'usage': data})
                    elif kind == 'session.error':
                        failed = True
                    elif kind == 'session.idle' and data.get('aborted'):
                        aborted = True
                if completion.done() and self.events.empty() and waiting is None:
                    break
                if completion.done() and self.events.empty() and waiting is not None and not waiting.done():
                    break
            try:
                final = completion.result()
            except Exception as error:
                if not (aborted or failed):
                    raise ConversationError('transport', 'Copilot turn completion is unconfirmed', uncertain=True) from error
                final = None
            if final is not None:
                final_data = record(final.data)
                output.replace(final_data.get('content', ''), final_data.get('messageId'))
            status = 'cancelled' if aborted else 'failed' if failed else 'completed'
            return dict(status=status, stop_reason=status,
                error={'code': 'provider', 'message': 'Copilot reported a turn failure'} if failed else None, **output.result())
        finally:
            if waiting is not None:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            if not completion.done():
                completion.cancel()
            await asyncio.gather(completion, return_exceptions=True)
            self.events = None

    async def cancel(self):
        self.cancel_requested = True
        await self.session.abort()

    async def steer(self, text, input_id):
        raise ConversationError('unsupported', 'Copilot steering has not been qualified')

    def commands(self):
        from .sdk_commands import catalog
        return catalog('copilot-sdk', self.settings)

    async def inspect_command(self, name):
        if name in ('context', 'usage'):
            return {'source': 'sdk-observed', 'usage': self.last_usage, 'model': self.model}
        if name == 'skills':
            from .sdk_commands import skills
            return skills(self.settings)
        if name == 'mcp':
            return {'source': 'captured-configuration', 'live_status_available': False,
                    'servers': (self.settings.get('mcp') or {}).get('servers', [])}
        raise ConversationError('unsupported', 'No Copilot SDK command mapping')

    async def close(self):
        if self.unsubscribe:
            self.unsubscribe()
        if self.client:
            await self.client.stop()
