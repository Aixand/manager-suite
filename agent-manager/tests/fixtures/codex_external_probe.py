#!/usr/bin/env python3
"""Synthetic stdio peer for the verification helper; never contacts a service."""
import json
import os
from pathlib import Path
import sys

token, stage = None, 0
for line in sys.stdin:
    item = json.loads(line)
    method = item['method']
    if method == 'initialized':
        assert stage == 1
        continue
    if method == 'initialize':
        assert stage == 0 and item['params']['capabilities']['experimentalApi']
        assert 'OPENAI_API_KEY' not in os.environ
        stage = 1
        result = {'userAgent': 'fixture/0.162.1 (test)'}
    elif method == 'account/login/start':
        assert stage == 1 and item['params']['type'] == 'chatgptAuthTokens'
        assert set(item['params']) == {'type', 'accessToken', 'chatgptAccountId'}
        stage = 2
        token = item['params']['accessToken']
        result = {'type': 'chatgptAuthTokens'}
        if token == 'fixture-persist-token': (Path(os.environ['CODEX_HOME']) / 'auth.json').write_text(token)
    elif method == 'account/rateLimits/read':
        assert stage == 2
        stage = 3
        if token == 'fixture-refresh-token':
            print(json.dumps({'id': 100, 'method': 'account/chatgptAuthTokens/refresh',
                'params': {'reason': 'unauthorized'}}), flush=True)
            continue
        if token == 'fixture-error-token':
            print(json.dumps({'id': item['id'], 'error': {'code': -32603, 'message': token}}), flush=True)
            continue
        result = {'rateLimitsByLimitId': {'codex': {
            'primary': {'usedPercent': 25, 'windowDurationMins': 10080, 'resetsAt': 1900000000},
            'secondary': None, 'secret': token}}}
        if token == 'fixture-malformed-token': result['rateLimitsByLimitId']['codex']['primary']['usedPercent'] = 200
    else: raise AssertionError('Unexpected method: ' + method)
    print(json.dumps({'id': item['id'], 'result': result}), flush=True)
