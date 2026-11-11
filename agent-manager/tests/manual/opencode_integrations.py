"""Verify the production OpenCode metadata bridge with disposable native homes.

Run: python3 tests/manual/opencode_integrations.py
Requires a qualified OpenCode executable and permission to listen on localhost.
Uses synthetic environment connections; all remote quota readers are disabled.
No sign-in, model turn, credential transfer, or existing profile is involved.
"""
import argparse
from contextlib import contextmanager
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import OpenerDirector

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent_manager import Error, Manager
from agent_manager.integrations import opencode, schema
from agent_manager.models.schema import ModelError
from agent_manager.services.loader import DEFINITIONS

SYNTHETIC = {'OPENAI_API_KEY': 'agent-manager-disposable-openai-key',
             'OPENROUTER_API_KEY': 'agent-manager-disposable-openrouter-key'}


def require(condition, message):
    if not condition: raise RuntimeError(message)


def prepare(base, program, timeout):
    user, workspace = base / 'user', base / 'workspace'
    user.mkdir(); workspace.mkdir()
    # Do not let project discovery inherit configuration above this fixture.
    for parent in workspace.parents:
        if any((parent / name).exists() for name in ('opencode.json', 'opencode.jsonc', '.opencode')):
            raise RuntimeError('An ancestor has OpenCode configuration; choose a clean --output location.')
    require(not Path('/etc/opencode').exists(), 'System OpenCode configuration is present; use a clean host.')
    config = base / 'providers.toml'
    config.write_text('[providers.opencode]\nexecutable=' + json.dumps(str(program)) + '\n')
    services = base / 'services'; services.mkdir()
    for name in ('openai', 'openrouter'):
        source = (DEFINITIONS / (name + '.toml')).read_text()
        for adapter in ('openai-native', 'openrouter-key'):
            source = source.replace('budget_adapter = "' + adapter + '"', 'budget_adapter = "disabled"')
        (services / (name + '.toml')).write_text(source)
    variables = {key: os.environ[key] for key in ('PATH', 'LANG', 'SYSTEMROOT') if key in os.environ}
    variables.update(HOME=str(user), USERPROFILE=str(user), TMPDIR=str(base), TEMP=str(base), TMP=str(base),
        XDG_CONFIG_HOME=str(user / '.config'), XDG_DATA_HOME=str(user / '.local/share'),
        XDG_STATE_HOME=str(user / '.local/state'), XDG_CACHE_HOME=str(user / '.cache'),
        OPENCODE_CONFIG_CONTENT='{}', OPENCODE_DISABLE_AUTOUPDATE='true',
        OPENCODE_DISABLE_MODELS_FETCH='true', DISABLE_AUTOUPDATER='1')
    with patch.dict(os.environ, variables, clear=True):
        manager = Manager('opencode', config, base / 'state')
        for profile in ('empty', 'synthetic', 'other'): manager.new_profile(profile)
        manager.alias_profile('alias', 'synthetic')
    manager.provider_spec = replace(manager.provider_spec,
        integrations=replace(manager.provider_spec.integrations, timeout=timeout))
    require(all(auth.budget_adapter == 'disabled' for rule in manager.provider_spec.integrations.rules
        for auth in rule.authentication), 'Remote quota readers must be disabled for this check.')
    require(manager.home('alias') == manager.home('synthetic'), 'Alias changed the native home.')
    require(len({manager.home(name) for name in ('empty', 'synthetic', 'other')}) == 3,
        'Disposable profiles must have separate native homes.')
    return manager, workspace, variables


@contextmanager
def diagnostics(path):
    """Keep native diagnostics only for this credential-free manual check."""
    original = subprocess.Popen
    original_open = OpenerDirector.open
    recorded = set()
    with path.open('wb') as stream:
        def launch(*args, **kwargs):
            kwargs['stderr'] = stream
            return original(*args, **kwargs)
        def open_request(opener, request, *args, **kwargs):
            try: return original_open(opener, request, *args, **kwargs)
            except HTTPError as error:
                # This opt-in fixture has cleared ambient credentials and uses
                # only synthetic connections. Keep HTTP diagnostics here rather
                # than reflecting native messages in production caches.
                raw = error.read(65536)
                try: body = json.loads(raw)
                except (ValueError, UnicodeError): body = {'unrecognized_body': True}
                row = dict(status=error.code, response=body)
                encoded = json.dumps(row, sort_keys=True)
                if encoded not in recorded:
                    recorded.add(encoded)
                    with path.with_suffix('.http-errors.jsonl').open('a') as output:
                        output.write(encoded + '\n')
                error.close()
                raise HTTPError(error.url, error.code, error.msg, error.headers, io.BytesIO(raw)) from None
        with patch('agent_manager.providers.opencode.subprocess.Popen', side_effect=launch), \
                patch.object(OpenerDirector, 'open', open_request):
            yield


def check(manager, profile, workspace, variables, *, synthetic=False):
    selected = dict(variables, **SYNTHETIC) if synthetic else variables
    policy = manager.provider_spec.integrations
    with patch.dict(os.environ, selected, clear=True):
        native, bridge, version = opencode.read(manager, manager.home(profile), workspace, policy)
    require(bridge is not None, 'The bundled native plugin did not register its RPC.')
    data = schema.validate_data(schema.normalize(native, bridge, policy, 0))
    rows = {row['id']: row for row in data['integrations']}
    for key, cli in (('openai', ['codex']), ('openrouter', [])):
        require(key in rows, 'A reviewed native integration was not discovered: ' + key)
        row = rows[key]
        require(row['recognized'] and row['service_id'] == key, 'Native service matching failed: ' + key)
        require(row['cli_providers'] == cli, 'Related CLI matching failed: ' + key)
        if synthetic:
            variable = 'OPENAI_API_KEY' if key == 'openai' else 'OPENROUTER_API_KEY'
            require(row['reviewed'], 'Native runtime or endpoint did not match: ' + key)
            require(row['active_connection'] == dict(id='env:' + variable, type='env', method='key'),
                'Synthetic environment connection was not selected: ' + key)
            require(row['budget_kind'] == ('api-billing' if key == 'openai' else 'credits'),
                'Authentication budget classification failed: ' + key)
            require(row['budget']['reason'] == 'reader-unavailable', 'A remote quota reader was enabled.')
        else:
            require(row['active_connection'] is None, 'An empty disposable home has an unexpected connection.')
        require(not row['budget']['buckets'], 'A metadata-only check returned quota observations.')
    encoded = json.dumps(data)
    require(not any(value in encoded for value in SYNTHETIC.values()), 'A synthetic credential crossed the native boundary.')
    return dict(profile=profile, status='passed', version=version, bridge_available=True,
        services=sorted(key for key in rows if rows[key]['recognized']),
        synthetic_connections=synthetic)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', default='opencode')
    parser.add_argument('--output', type=Path, help='New directory for disposable homes, native logs and results.json')
    parser.add_argument('--timeout', type=int, default=45, help='Seconds per native inspection (1–120)')
    parser.add_argument('--prepare-only', action='store_true', help='Prepare disposable configuration without running OpenCode')
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 120: parser.error('--timeout must be between 1 and 120')
    program = shutil.which(args.executable)
    if not program: parser.error('OpenCode executable was not found')
    base = args.output.expanduser().absolute() if args.output else Path(tempfile.mkdtemp(prefix='agent-opencode-integrations-'))
    if args.output: base.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = dict(status='failed', provider='opencode', production_adapter=True, cases=[],
        real_logins=0, model_calls=0, quota_readers_disabled=True, authenticated_quota_verified=False,
        native_plugin_verified=False)
    print('Disposable check: ' + str(base), flush=True)
    code = 1
    try:
        manager, workspace, variables = prepare(base, Path(program).resolve(), args.timeout)
        if args.prepare_only:
            report['status'], code = 'prepared', 0
        else:
            for profile, synthetic in (('empty', False), ('alias', True), ('synthetic', False), ('other', False)):
                log = base / (profile + '.log')
                with diagnostics(log): result = check(manager, profile, workspace, variables, synthetic=synthetic)
                report['cases'].append(result)
                print(profile + ': passed', flush=True)
            report.update(status='passed', native_plugin_verified=True)
            code = 0
    except (Error, ModelError, OSError, RuntimeError, ValueError) as error:
        # Never treat an unsupported version, absent plugin or denied socket as
        # a successful native qualification. Raw diagnostics remain disposable.
        logs = '\n'.join(path.read_text(errors='replace')[:65536] for path in base.glob('*.log'))
        denied = 'listen' in logs.lower() and any(marker in logs.lower() for marker in ('eperm', 'eacces', 'operation not permitted'))
        report.update(status='blocked' if denied else 'failed',
            error='Local listening sockets are denied by this environment.' if denied else str(error),
            error_code=error.code if isinstance(error, ModelError) else 'check-failed')
        code = 2 if denied else 1
    except KeyboardInterrupt:
        report.update(status='interrupted', error='Interrupted by user.')
        code = 130
    finally:
        report['http_diagnostics'] = [path.name for path in sorted(base.glob('*.http-errors.jsonl'))]
        report['native_logs'] = [str(path.relative_to(base)) for path in sorted(
            manager.managed_homes.glob('*/data/opencode/log/*.log'))[:16]] if 'manager' in locals() else []
        (base / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
        print('Result: ' + report['status'] + '; report: ' + str(base / 'results.json'), flush=True)
        if report.get('error'): print(report['error'], file=sys.stderr)
    return code


if __name__ == '__main__': raise SystemExit(main())
