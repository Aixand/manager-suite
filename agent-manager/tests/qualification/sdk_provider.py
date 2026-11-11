#!/usr/bin/env python3
"""Offline Codex fixture: native quota RPC plus a deterministic terminal loop."""
import json
import os
from pathlib import Path
import sys
import time

if sys.argv[1:] in (['--version'], ['--binary-version']):
    print('Native CLI 0.164.0')
    raise SystemExit(0)

home = Path(os.environ['CODEX_HOME'])
if sys.argv[1:2] == ['app-server']:
    for line in sys.stdin:
        message = json.loads(line)
        method = message['method']
        if method not in ('initialize', 'initialized', 'account/rateLimits/read', 'model/list'):
            raise ValueError(f'Unexpected fixture RPC: {method}')
        if 'id' not in message:
            continue
        if method == 'account/rateLimits/read':
            result = json.loads((home / 'quota-fixture.json').read_text())
        elif method == 'model/list':
            result = {'data': [{'id': 'native-model', 'model': 'native-model',
                               'displayName': 'Native model', 'isDefault': True}]}
        else:
            result = {'version': '0.164.0', 'protocolVersion': 3}
        print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
else:
    (home / 'launch.json').write_text(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd()}))
    messages = []

    def ready():
        print('\x1b[2J\x1b[H' + '\n'.join(messages) + '\n› \n  gpt-test high ' + os.getcwd(), flush=True)

    ready()
    for line in sys.stdin:
        print('\x1b[2J\x1b[HWorking\nesc to interrupt', flush=True)
        time.sleep(.35)
        line = line.replace('\x1b[200~', '').replace('\x1b[201~', '')
        messages.append('RESULT:' + line.strip())
        ready()
