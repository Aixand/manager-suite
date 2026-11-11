"""Qualify an experimental OpenCode OAuth -> Codex quota handoff.

Default: synthetic, offline check with the real Codex binary.
--login: create a disposable OpenCode home, let the user sign in, then read quota.
--live --reuse DIR: repeat the read using this check's existing disposable home.
--production --reuse DIR: verify normal budget, CLI, SDK, MCP and alias-cache paths.
No existing Manager profiles/configuration, model turns, or refresh-token transfer.
"""
import argparse
import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent_manager import Error, Manager
from agent_manager.integrations.opencode import rpc_failure
from agent_manager.providers.config import load_registry
from agent_manager.providers.opencode import metadata_server
from tests.manual.opencode_integrations import prepare
from agent_manager.services.loader import DEFINITIONS

NATIVE = Path(__file__).with_name('openai_bridge')
SCHEMA = 'agent-manager/openai-bridge-check-1'


class VerificationError(RuntimeError):
    """Only fixed diagnostic codes may be copied into a credentialed report."""
    pass


def environment(base):
    keys = ('PATH', 'LANG', 'SYSTEMROOT', 'SystemRoot', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
            'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR')
    result = {key: os.environ[key] for key in keys if key in os.environ}
    user = base / 'user'
    result.update(HOME=str(user), USERPROFILE=str(user), TMPDIR=str(base), TEMP=str(base), TMP=str(base),
        XDG_CONFIG_HOME=str(user / '.config'), XDG_DATA_HOME=str(user / '.local/share'),
        XDG_STATE_HOME=str(user / '.local/state'), XDG_CACHE_HOME=str(user / '.cache'),
        OPENCODE_CONFIG_CONTENT='{}', OPENCODE_DISABLE_AUTOUPDATE='true', OPENCODE_DISABLE_MODELS_FETCH='true')
    return result


def program(value, fallback):
    path = shutil.which(value or fallback)
    if not path: raise VerificationError('executable-unavailable')
    return Path(path).resolve()


def setup(base, opencode, codex, reuse):
    marker = base / 'verification.json'
    variables = environment(base)
    if reuse:
        saved = json.loads(marker.read_text())
        if saved.get('schema') != SCHEMA: raise VerificationError('unrecognized-disposable-directory')
        opencode, codex = Path(saved['opencode']), Path(saved['codex'])
        # Reconstruct only this disposable Manager, never global configuration.
        with patch.dict(os.environ, variables, clear=True):
            manager = Manager('opencode', base / 'providers.toml', base / 'state')
    else:
        manager, _, _ = prepare(base, opencode, 30)
        marker.write_text(json.dumps(dict(schema=SCHEMA, opencode=str(opencode), codex=str(codex))) + '\n')
    if not manager.home('empty').resolve().is_relative_to(base.resolve()):
        raise VerificationError('profile-outside-disposable-directory')
    if (base / 'providers.toml').is_symlink(): raise VerificationError('unrecognized-disposable-directory')
    (base / 'providers.toml').write_text('[providers.opencode]\nexecutable=' + json.dumps(str(opencode))
        + '\n[providers.codex]\nexecutable=' + json.dumps(str(codex)) + '\n')
    return manager, variables, opencode, codex


def check_socket():
    with socket.socket() as listener: listener.bind(('127.0.0.1', 0))


def live(manager, variables, base, codex, timeout):
    home, workspace = manager.home('empty'), base / 'workspace'
    content = json.dumps({'plugins': [str(NATIVE.resolve())]})
    variables = variables | {'AGENT_MANAGER_OPENAI_CODEX': json.dumps(dict(executable=str(codex),
        versions=list(load_registry(manager.config_path).resolve('codex').budget.versions)))}
    with patch.dict(os.environ, variables, clear=True):
        with metadata_server(manager, home, {'OPENCODE_CONFIG_CONTENT': content},
                cwd=workspace, end=time.monotonic() + timeout) as request:
            until = time.monotonic() + 5
            while True:
                try:
                    value = request('/api/rpc/aixand.agent.budgets.v2/read', {'input': {'rules': []}}).get('output')
                    if not isinstance(value, dict) or value.get('schema') != 'agent-manager/openai-bridge-verification-1':
                        raise VerificationError('rpc-invalid-response')
                    return value
                except HTTPError as failure:
                    if failure.code == 400:
                        code = rpc_failure(failure)
                        if code: raise VerificationError(code) from None
                    elif failure.code == 404: failure.close()
                    else:
                        failure.close()
                        raise VerificationError('private-request-failed') from None
                    if time.monotonic() >= until: raise VerificationError('rpc-unavailable')
                    time.sleep(.1)


def production(base, variables, model=None):
    """The normal interfaces read only this fixture's native login and caches."""
    from agent_manager import Client
    from agent_manager.interfaces.cli.dispatch import execute
    from agent_manager.interfaces.management_mcp import LocalBackend
    from agent_manager.budgets.ranking import score
    from agent_manager.budgets.service import selection_policy
    with patch.dict(os.environ, variables, clear=True):
        service = base / 'services/openai.toml'
        if service.is_symlink() or not service.resolve().is_relative_to(base):
            raise VerificationError('unrecognized-disposable-directory')
        service.write_text((DEFINITIONS / 'openai.toml').read_text())
        manager = Manager('opencode', base / 'providers.toml', base / 'state')
        workspace = base / 'workspace'
        if model is None:
            discovery = manager.models('empty', cwd=workspace)
            if discovery.get('error'):
                from agent_manager.models.schema import ModelError
                code = discovery['error']['code']
                if code in ModelError.MESSAGES: raise VerificationError('model-discovery-' + code)
                raise VerificationError('model-discovery-failed')
            models = discovery['models']
            model = next((row['id'] for row in models if row['provider_id'] == 'openai'
                and row['availability'] == 'listed'), None)
        if not model or not model.startswith('openai/'): raise VerificationError('openai-model-unavailable')
        if 'quota-alias' not in manager.state()['profiles']: manager.alias_profile('quota-alias', 'empty')
        fresh = manager.budget('empty', cwd=workspace, model=model, refresh=True)
        cached = manager.budget('quota-alias', cwd=workspace, model=model, cached=True)
        if (fresh['status'] not in ('ok', 'partial') or not fresh['buckets']
                or cached['observed_at'] != fresh['observed_at'] or cached['buckets'] != fresh['buckets']):
            return dict(status='failed', reason='production-quota-unavailable', budget=fresh,
                authenticated_quota_verified=False, allowance_scope_verified=False)
        with redirect_stdout(io.StringIO()) as output:
            execute(['--config', str(base / 'providers.toml'), '--home', str(base / 'state'),
                'opencode', 'budget', 'quota-alias', '--cwd', str(workspace), '--model', model, '--cached', '--json'])
        cli = json.loads(output.getvalue())
        async def interfaces():
            client = Client('opencode', config=base / 'providers.toml', root=base / 'state', runtime=object())
            sdk = await client.budget('quota-alias', cwd=workspace, model=model, cached=True)
            mcp = await LocalBackend(config=base / 'providers.toml', home=base / 'state').execute('get_budget',
                dict(provider='opencode', profile='quota-alias', cwd=str(workspace), model=model, cached=True), 'verify-budget')
            return sdk, mcp['budget']
        sdk, mcp = asyncio.run(interfaces())
        if any(row['observed_at'] != fresh['observed_at'] or row['buckets'] != fresh['buckets'] for row in (cli, sdk, mcp)):
            raise VerificationError('interface-budget-mismatch')
        try: score(fresh, selection_policy(manager), model=model, allow_exhausted=True)
        except ValueError: qualified = False
        else: qualified = True
        return dict(status='production-verified', authenticated_quota_verified=True,
            allowance_scope_verified=qualified, model_calls=0, budget=fresh,
            checks=dict(alias_cache=True, cli=True, sdk=True, mcp=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--login', action='store_true', help='Sign in natively in a disposable home, then read quota.')
    actions.add_argument('--live', action='store_true', help='Read the disposable home\'s selected legacy ChatGPT connection.')
    actions.add_argument('--production', action='store_true', help='Verify normal budget interfaces using the disposable login.')
    locations = parser.add_mutually_exclusive_group()
    locations.add_argument('--output', type=Path, help='Create this new disposable directory.')
    locations.add_argument('--reuse', type=Path, help='Reuse a directory created by this check.')
    parser.add_argument('--method', choices=('chatgpt-browser', 'chatgpt-headless'), default='chatgpt-headless')
    parser.add_argument('--opencode', help='Qualified OpenCode executable (2.0.18).')
    parser.add_argument('--codex', help='Qualified Codex executable (0.162.1 or 0.164.0).')
    parser.add_argument('--node', help='Node.js executable for the offline check.')
    parser.add_argument('--model', help='OpenAI model route for the production check; otherwise discovered natively.')
    parser.add_argument('--timeout', type=float, default=40)
    args = parser.parse_args(argv)
    if args.reuse:
        base = args.reuse.expanduser().resolve()
    elif args.output:
        base = args.output.expanduser().resolve(); base.mkdir(mode=0o700)
    else: base = Path(tempfile.mkdtemp(prefix='agent-openai-bridge-'))
    report = dict(schema=SCHEMA, status='failed', model_calls=0, authenticated_quota_verified=False,
                  allowance_scope_verified=False)
    print('Disposable check: ' + str(base), flush=True)
    code = 1
    try:
        manager, variables, opencode, codex = setup(base, program(args.opencode, 'opencode'),
            program(args.codex, 'codex'), bool(args.reuse))
        version = subprocess.run([str(opencode), '--version'], cwd=base, env=variables,
            capture_output=True, text=True, timeout=10)
        if version.returncode or version.stdout.strip() != 'opencode v2.0.18':
            raise VerificationError('unsupported-opencode-version')
        if args.login or args.live or args.production:
            check_socket()
            if args.login:
                with patch.dict(os.environ, variables, clear=True):
                    login_env = manager.launch_environment(manager.home('empty'), cwd=base / 'workspace')
                command = [str(opencode), 'auth', 'login', 'openai', '--standalone', '--method', args.method]
                if subprocess.call(command, cwd=base / 'workspace', env=login_env):
                    raise VerificationError('native-login-failed')
            native = production(base, variables, args.model) if args.production else live(manager, variables, base, codex, args.timeout)
            report.update(native=native, status=native['status'],
                authenticated_quota_verified=native['authenticated_quota_verified'],
                allowance_scope_verified=native['allowance_scope_verified'])
            code = 0 if native['authenticated_quota_verified'] else 3 if native['status'] == 'login-required' else 1
        else:
            node = program(args.node, 'node')
            result = subprocess.run([str(node), str(NATIVE / 'offline.mjs'), str(codex),
                json.dumps(list(load_registry(manager.config_path).resolve('codex').budget.versions))], cwd=base,
                env=variables, capture_output=True, text=True, timeout=args.timeout)
            try: observation = json.loads(result.stdout)
            except ValueError: raise VerificationError('offline-invalid-report') from None
            report.update(offline=observation, status=observation['status'])
            code = 0 if result.returncode == 0 and observation['status'] == 'offline-handoff-verified' else 1
            print('To verify a real login in this disposable home:', flush=True)
            print(shlex.join([sys.executable, str(Path(__file__).resolve()), '--reuse', str(base), '--login']), flush=True)
    except PermissionError:
        report.update(status='blocked', reason='Local listening sockets or required files are unavailable in this environment.')
        code = 2
    except VerificationError as failure:
        report.update(status='failed', reason=str(failure))
    except (Error, RuntimeError, ValueError, OSError, subprocess.TimeoutExpired, KeyError):
        # This check handles real credentials in a native child. Never publish
        # exception text, raw HTTP errors, child logs, environment, or RPC frames.
        report.update(status='failed', reason='Verification could not complete; no authenticated support is established.')
    except KeyboardInterrupt:
        report.update(status='interrupted'); code = 130
    (base / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Result: ' + report['status'] + '; report: ' + str(base / 'results.json'), flush=True)
    if report.get('reason'): print(report['reason'], flush=True)
    return code


if __name__ == '__main__': raise SystemExit(main())
