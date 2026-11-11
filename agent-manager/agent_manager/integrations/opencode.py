"""Native integration introspection and a private, credential-preserving RPC."""
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError

from agent_manager.budgets.models import BudgetError
from agent_manager.budgets.transport import deadline, remaining, run
from agent_manager.errors import Error
from agent_manager.models.schema import ModelError
from agent_manager.providers.opencode import CatalogError, metadata_server
from .schema import IntegrationError
from .dependencies import codex as codex_dependency

NATIVE = Path(__file__).with_name('native')


def rpc_failure(error):
    """Project only fixed native error types; never expose upstream messages."""
    try:
        raw = error.read(65537)
        value = json.loads(raw) if len(raw) <= 65536 else None
        kind = value.get('type') if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError): kind = None
    finally: error.close()
    if kind == 'rpc.unavailable': return None
    if kind == 'rpc.invalid_input': return 'rpc-invalid-input'
    if kind == 'rpc.method_not_found': return 'rpc-method-not-found'
    return 'rpc-rejected'


def overrides(manager, home, cwd, policy=None):
    value = manager.launch_environment(home, cwd=cwd).get('OPENCODE_CONFIG_CONTENT', '{}')
    try:
        if len(value) > 4 * 1024 * 1024: raise ValueError()
        config = json.loads(value)
        if not isinstance(config, dict): raise ValueError()
        plugins = config.setdefault('plugins', [])
        if not isinstance(plugins, list): raise ValueError()
        plugins.append(str(NATIVE.resolve()))
    except (ValueError, TypeError):
        raise Error('Inline OpenCode configuration must be a JSON object for the private metadata probe') from None
    # Native global/project settings and credentials stay in place. This extra
    # plugin is injected only into the short-lived metadata server process.
    return {'OPENCODE_CONFIG_CONTENT': json.dumps(config),
        'AGENT_MANAGER_OPENAI_CODEX': json.dumps(codex_dependency(manager, policy or manager.provider_spec.integrations))}


def read(manager, home, cwd, policy):
    end = deadline(policy.timeout)
    try:
        output, code = run(manager, home, ['--version'], end, cwd=cwd)
        match = re.search(rb'(?:^|\s)v?(\d+\.\d+\.\d+)(?:\s|$)', output)
        if code or not match or match[1].decode() not in policy.versions: raise ModelError('unsupported')
        version = match[1].decode()
        with metadata_server(manager, home, overrides(manager, home, cwd, policy), cwd=cwd, end=end,
                startup_timeout=remaining(end)) as request:
            native = request('/api/integration')
            # First-party plugins can still be settling when HTTP starts.
            until = min(end, time.monotonic() + 5)
            while True:
                try:
                    response = request('/api/rpc/aixand.agent.budgets.v2/read',
                        {'input': {'rules': [row.native() for row in policy.rules]}})
                    bridge = response.get('output')
                    if not isinstance(bridge, dict) or bridge.get('schema') != 'agent-manager/opencode-bridge-2' or bridge.get('version') != version:
                        raise ModelError('invalid-response')
                    return native, bridge, version
                except HTTPError as error:
                    if error.code not in (400, 404): raise
                    if error.code == 400:
                        failure = rpc_failure(error)
                        if failure: raise IntegrationError(failure) from None
                    else: error.close()
                    if time.monotonic() >= until: return native, None, version
                    time.sleep(min(.25, remaining(end)))
    except BudgetError as error: raise ModelError(error.code) from None
    except CatalogError as error: raise ModelError(error.code) from None
    except ModelError: raise
    except HTTPError as error:
        raise ModelError('unsupported' if error.code == 404 else 'authentication' if error.code in (401, 403) else 'unavailable') from None
    except TimeoutError: raise ModelError('timeout') from None
    except (OSError, Error): raise ModelError('unavailable') from None
    except (ValueError, TypeError, KeyError): raise ModelError('invalid-response') from None
