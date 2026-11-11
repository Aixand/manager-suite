"""Human CLI output; structured service and SDK results remain unchanged."""
from agent_manager.interfaces.cli.tui import clean


def display(manager, args, result):
    if result is None:
        print('No profile instruction binding.' if getattr(args, 'instruction_action', None) != 'clear' else 'Cleared profile instruction binding.')
        return
    if isinstance(result, list):
        if not result:
            print(f'No {manager.provider} profiles. Run: agent {manager.provider} onboarding')
            return
        rows = [('Profile', 'Default', 'Login (last checked)', 'Model')]
        rows += [(row['profile'], 'Yes' if row['preferred'] else '',
                  row['login']['state'] if row['login']['state'] != 'unknown' else 'Unchecked / unknown',
                  row['model'] or 'Provider default') for row in result]
        widths = [max(len(clean(row[index])) for row in rows) for index in range(4)]
        for row in rows:
            print('  '.join(clean(value).ljust(width) for value, width in zip(row, widths)).rstrip())
        return
    action = getattr(args, 'action', None)
    if action in ('new', 'connect'):
        print(f'{"Created" if action == "new" else "Connected"} {manager.provider} profile "{clean(result["profile"])}".')
        print(f'Run it with: agent {manager.provider} run {clean(result["profile"])}')
    elif 'profile' in result and 'home' in result:
        print(f'{manager.provider} profile: {clean(result["profile"])}')
        print(f'Native home: {clean(result["home"])}')
        print(f'Default: {"yes" if result["preferred"] else "no"}')
        print(f'Login (last checked): {clean(result["login"]["state"])}')
        print(f'Model: {clean(result["model"] or "Provider default")}')
    else:
        for key, value in result.items():
            if isinstance(value, dict):
                print(f'{clean(key)}:')
                for field, item in value.items(): print(f'  {clean(field)}: {clean(item)}')
            else: print(f'{clean(key)}: {clean(value)}')
