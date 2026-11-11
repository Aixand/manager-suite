"""Use the public SDK with existing profiles and an already running Undercurrent daemon."""
import argparse
import asyncio
import json
from pathlib import Path
import uuid

from agent_manager import Client


async def main(args):
    import undercurrent

    connection = {}
    if args.runtime_home is not None:
        connection['home'] = args.runtime_home
    if args.bridge is not None:
        connection['bridge'] = args.bridge
    async with undercurrent.Client(**connection) as runtime:
        async with Client(args.provider, runtime=runtime, config=args.config, root=args.root) as agents:
            if args.command == 'inventory':
                print(json.dumps(dict(profiles=await agents.profiles(), environments=await agents.environments(),
                                      budgets=await agents.budgets(cached=True),
                                      sessions=await agents.sessions(cwd=args.cwd)), indent=2))
                return
            if args.command == 'recover':
                agent = await agents.get(json.loads(args.reference.read_text()))
            else:
                key = args.idempotency_key or str(uuid.uuid4())
                print('Idempotency key:', key, flush=True)
                options = dict(idempotency_key=key)
                if args.command == 'continue':
                    if args.profile:
                        options['profile'] = args.profile
                    else:
                        options['auto'] = True
                    agent = await agents.continue_session(args.session, **options)
                elif args.profile:
                    agent = await agents.spawn(profile=args.profile, cwd=args.cwd, **options)
                else:
                    agent = await agents.start(cwd=args.cwd, **options)
            try:
                if args.command != 'recover':
                    args.reference.write_text(json.dumps(agent.reference.to_dict(), indent=2))
                print(json.dumps((await agent.inspect()).to_dict(), indent=2))
                if args.message is not None:
                    await agent.wait(lifecycle='running', interaction='available', timeout=args.timeout)
                    receipt = await agent.send_input(args.message, delivery_id=args.delivery_id)
                    print('Delivered input:', receipt)
                    # Delivery acknowledges terminal input; it does not prove task completion.
                print((await agent.read_screen()).decode('utf-8', 'replace'))
            finally:
                if args.stop:
                    await agent.stop()
                    await agent.wait(lifecycle=('stopped', 'exited', 'failed'), timeout=args.timeout)
            # Context exit detaches; the execution keeps running unless --stop was supplied.


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('inventory', 'launch', 'continue', 'recover'))
    parser.add_argument('--provider', default='codex')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--runtime-home', type=Path)
    parser.add_argument('--bridge')
    parser.add_argument('--cwd', type=Path, default=Path.cwd())
    parser.add_argument('--profile', help='Explicit profile; otherwise select automatically')
    parser.add_argument('--session', help='Saved session ref from inventory (required for continue)')
    parser.add_argument('--reference', type=Path, default=Path('execution.json'))
    parser.add_argument('--idempotency-key', help='Retain and reuse this UUID when retrying a launch')
    parser.add_argument('--message')
    parser.add_argument('--delivery-id', help='Stable terminal input delivery ID')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--stop', action='store_true', help='Explicitly stop the selected execution before detaching')
    args = parser.parse_args()
    if args.command == 'continue' and not args.session:
        parser.error('continue requires --session')
    asyncio.run(main(args))
