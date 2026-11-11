"""Offline native budget protocols; refuses session creation or model prompts."""
import json
import os
from pathlib import Path
import sys
import time

provider = Path(sys.argv[0]).name
versions = {'codex': '0.164.0', 'claude': '2.1.302', 'copilot': '1.0.104', 'antigravity': '1.4.8', 'opencode': '2.0.18'}
version = os.environ.get('BUDGET_FIXTURE_VERSION', versions[provider])
if sys.argv[1:] in (['--version'], ['--binary-version']):
    print('Native CLI ' + version)
    raise SystemExit(0)

home_key = {'codex': 'CODEX_HOME', 'claude': 'CLAUDE_CONFIG_DIR', 'copilot': 'COPILOT_HOME'}
home = Path(os.environ.get(home_key.get(provider, ''), str(Path.home() / '.gemini/antigravity-cli')))
if provider == 'claude' and '--input-format' in sys.argv:
    message = json.loads(sys.stdin.buffer.readline())
    assert message['type'] == 'control_request' and message['request']['subtype'] == 'initialize'
    print(json.dumps({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': message['request_id'], 'response': {} if (home / 'alias-fallback').exists() else {'models': [{'value': 'sonnet', 'displayName': 'Sonnet'}]}}}))
    raise SystemExit(0)
if provider == 'antigravity' and sys.argv[1:] == ['models']:
    print('gemini-test\tGemini Test'); raise SystemExit(0)
is_probe = (sys.argv[1:2] == ['app-server'] or '--headless' in sys.argv
            or '/usage' in sys.argv)
if not is_probe:
    # A disposable launch records argv/home and exits; it never calls a model.
    (home / 'launched.json').write_text(json.dumps({'args': sys.argv[1:], 'home': str(home)}))
    raise SystemExit(0)
mode = os.environ.get('BUDGET_FIXTURE_MODE', '')
if mode == 'timeout' and provider not in ('codex', 'copilot'): time.sleep(10)
if mode == 'oversized':
    sys.stdout.write('x' * (5 * 1024 * 1024)); sys.stdout.flush(); raise SystemExit(0)
reset = time.time() + 86400

def quota():
    fixture = home / 'quota-fixture.json'
    if fixture.exists(): return json.loads(fixture.read_text())
    if provider == 'codex':
        return {'rateLimitsByLimitId': {'codex': {'primary': {'usedPercent': 25, 'resetsAt': reset,
            'windowDurationMins': 300}, 'secondary': {'usedPercent': 100, 'resetsAt': reset}}}}
    return {'quotaSnapshots': {'premium_interactions': {'entitlementRequests': 100,
        'usedRequests': 25, 'remainingPercentage': 75, 'resetDate': '2100-01-01T00:00:00Z'}}}

if provider in ('codex', 'copilot'):
    if provider == 'copilot': assert '--no-auto-login' not in sys.argv
    framed = provider == 'copilot'
    while True:
        if framed:
            header = sys.stdin.buffer.readline()
            if not header: break
            assert header.startswith(b'Content-Length: ')
            assert sys.stdin.buffer.readline() == b'\r\n'
            message = json.loads(sys.stdin.buffer.read(int(header.split(b':')[1])))
        else:
            line = sys.stdin.buffer.readline()
            if not line: break
            message = json.loads(line)
        method = message['method']

        assert method in ('initialize', 'initialized', 'account/rateLimits/read', 'status.get', 'account.getQuota', 'model/list', 'models.list')
        if 'id' not in message: continue
        if method in ('model/list', 'models.list'):
            result = {'data': [{'id': 'native-model', 'model': 'native-model', 'displayName': 'Native model', 'isDefault': True}]} if provider == 'codex' else {'models': [{'id': 'native-model', 'name': 'Native model'}]}
            with (home / 'model-probes.jsonl').open('a') as log: log.write(json.dumps(method) + '\n')
        elif method in ('initialize', 'status.get'):
            result = {'version': version, 'protocolVersion': 3}
        else:
            with (home / 'probe-log.jsonl').open('a') as log:
                log.write(json.dumps(dict(arguments=sys.argv[1:], home=str(home), ambient_credentials={'codex': 'OPENAI_API_KEY', 'copilot': 'GH_TOKEN'}[provider] in os.environ)) + '\n')
            with (home / 'methods.jsonl').open('a') as log:
                for item in (['initialize', 'initialized', method] if provider == 'codex' else ['status.get', method]): log.write(json.dumps(item) + '\n')
            if mode == 'timeout': time.sleep(10)
            result = quota()
        response = dict(jsonrpc='2.0', id=message['id'], result=result)
        if mode == 'auth-error' and method not in ('initialize', 'status.get'):
            response.pop('result'); response['error'] = {'code': 401, 'message': 'Unauthorized private-fixture-token'}
        data = json.dumps(response).encode()
        if framed: data = f'Content-Length: {len(data)}\r\n\r\n'.encode() + data
        else: data += b'\n'
        sys.stdout.buffer.write(data); sys.stdout.buffer.flush()
elif provider == 'claude':
    with (home / 'probe-log.jsonl').open('a') as log:
        log.write(json.dumps(dict(arguments=sys.argv[1:], home=str(home),
            ambient_credentials={'codex': 'OPENAI_API_KEY', 'claude': 'ANTHROPIC_API_KEY',
                'copilot': 'GH_TOKEN', 'antigravity': 'GEMINI_API_KEY'}[provider] in os.environ)) + '\n')
    assert sys.argv[-2:] == ['-p', '/usage']
    report = {'type': 'system', 'local_command_run': {'command': 'usage'},
        'usage_report': {'rate_limits': {'limits': [{'kind': 'session', 'percent': 25, 'resets_at': reset},
            {'kind': 'weekly_scoped', 'percent': 70, 'resets_at': reset,
             'scope': {'model': {'display_name': 'Opus'}}}]}}}
    final = {'type': 'result', 'local_command': 'usage', 'num_turns': 0,
        'duration_api_ms': 0, 'total_cost_usd': 0, 'is_error': False}
    if mode == 'model-output': final['num_turns'] = 1
    print(json.dumps(report)); print(json.dumps(final))
else:
    with (home / 'probe-log.jsonl').open('a') as log:
        log.write(json.dumps(dict(arguments=sys.argv[1:], home=str(home),
            ambient_credentials={'codex': 'OPENAI_API_KEY', 'claude': 'ANTHROPIC_API_KEY',
                'copilot': 'GH_TOKEN', 'antigravity': 'GEMINI_API_KEY'}[provider] in os.environ)) + '\n')
    assert sys.argv[1:] == ['-p', '/usage', '--output-format', 'json']
    print(json.dumps({'status': 'SUCCESS', 'num_turns': 0, 'usage': {'total_tokens': 0},
        'command': {'name': 'usage', 'data': {'groups': [{'name': 'Gemini', 'buckets': [
            {'id': 'gemini-weekly', 'window': 'weekly', 'remaining_fraction': .75, 'reset_time': reset}]}]}}}))
