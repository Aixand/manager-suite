"""Disposable native CLI double with OpenCode's import collision semantics."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

if sys.argv[1:] == ['--version']:
    print('opencode v2.0.18')
    raise SystemExit()

path = Path(os.environ['OPENCODE_DB'])
path.parent.mkdir(parents=True, exist_ok=True)
with (path.parent / 'commands.jsonl').open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\n')
with sqlite3.connect(path) as database:
    database.execute('CREATE TABLE IF NOT EXISTS session_v2(id TEXT PRIMARY KEY,title TEXT,time_updated INTEGER,directory TEXT,parent_id TEXT,time_archived INTEGER)')
    database.execute('CREATE TABLE IF NOT EXISTS projected(id TEXT PRIMARY KEY,payload TEXT)')
    database.execute('CREATE TABLE IF NOT EXISTS session_message(id TEXT PRIMARY KEY,session_id TEXT)')
    database.execute('CREATE TABLE IF NOT EXISTS credential(value TEXT)')
    args = sys.argv[1:]
    if args[:2] == ['session', 'export']:
        row = database.execute('SELECT payload FROM projected WHERE id=?', (args[2],)).fetchone()
        if row is None: raise SystemExit(1)
        payload = json.loads(row[0])
        payload['messages'] = [message for message in payload['messages']
            if not (message['type'] == 'assistant' and 'completed' not in message['time'])
            and not (message['type'] in ('shell', 'compaction') and message.get('status') == 'running')]
        print(json.dumps(payload))
    elif args[:2] == ['session', 'import']:
        payload = json.loads(Path(args[2]).read_text())
        sid = payload['info']['id']
        directory = args[args.index('--directory') + 1]
        payload['info'].update(location={'directory': directory})
        payload['info']['time']['updated'] = int(time.time() * 1000)
        if (path.parent / 'corrupt-import').exists(): payload['messages'][0]['text'] = 'CORRUPTED_IMPORT'
        database.execute('INSERT INTO session_v2 VALUES(?,?,?,?,NULL,NULL)',
            (sid, payload['info'].get('title'), payload['info']['time']['updated'], directory))
        for message in payload['messages']:
            database.execute('INSERT INTO session_message VALUES(?,?)', (message['id'], sid))
        database.execute('INSERT INTO projected VALUES(?,?)', (sid, json.dumps(payload)))
        print('Imported session: ' + sid)
        if (path.parent / 'fail-after-import').exists():
            database.commit()
            raise SystemExit(1)
    elif args[:1] in (['mini'], ['--standalone']):
        if '--session' in args: sid = args[args.index('--session') + 1]
        else: sid = next(item.split('=', 1)[1] for item in args if item.startswith('--session='))
        payload = json.loads(database.execute('SELECT payload FROM projected WHERE id=?', (sid,)).fetchone()[0])
        account = database.execute('SELECT value FROM credential').fetchone()[0]
        path.with_suffix('.observed.json').write_text(json.dumps(dict(id=sid, account=account,
            payload=payload, cwd=str(Path.cwd()), database=str(path), args=args)))
    else: raise SystemExit(2)
