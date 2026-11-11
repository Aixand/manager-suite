"""Interactive editor and CLI presentation over native instruction services."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from agent_manager.errors import Error
from agent_manager.providers.native_instructions import describe, read


def editor_command(*, picker=None):
    for variable in ('VISUAL', 'EDITOR'):
        value = os.environ.get(variable, '').strip()
        if value:
            try:
                command = shlex.split(value, posix=os.name != 'nt')
            except ValueError:
                raise Error(f'{variable} contains an invalid editor command') from None
            if os.name == 'nt':
                command = [part[1:-1] if len(part) > 1 and part[0] == part[-1] == '"' else part for part in command]
            if not command or not shutil.which(command[0]):
                raise Error(f'The editor configured in {variable} is unavailable')
            return command
    from agent_manager.interfaces.cli import tui
    candidates = [(label, path) for label in ('nano', 'vim', 'vi') if (path := shutil.which(label))]
    if not candidates: raise Error('No terminal editor found. Set VISUAL or EDITOR, then retry.')
    selected = (picker or tui.pick)('Choose an instruction editor', ['Editor', 'Executable'], candidates,
        'Set VISUAL or EDITOR to remember your editor preference')
    return None if selected is None else [candidates[selected][1]]


def edit(manager, profile=None, *, picker=None, expected_revision=None):
    from agent_manager.providers.native_instructions import inspect, write
    from agent_manager.storage.files import private_dir
    import tempfile
    target = manager.resolve_profile(profile) if profile is not None else None
    row = inspect(manager, target)
    if expected_revision is not None and row['revision'] != expected_revision:
        raise Error('Instruction file changed; refresh before editing')
    command = editor_command(picker=picker)
    if command is None: return None
    path = Path(row['path']).resolve()
    folder = manager.root / 'instruction-drafts'
    private_dir(folder)
    fd, temporary = tempfile.mkstemp(prefix='edit-', suffix='-' + path.name, dir=folder)
    draft = Path(temporary)
    with os.fdopen(fd, 'wb') as stream: stream.write(row['content'].encode('utf-8'))
    print(f'Editing {manager.provider} instruction draft ({row["scope"]}) for: {path}', file=sys.stderr)
    if row['override']:
        print('AGENTS.override.md takes precedence over AGENTS.md in this profile.', file=sys.stderr)
    if row['scope'] == 'shared-native':
        print('Antigravity native instructions are shared by all of its profiles.', file=sys.stderr)
    try:
        result = subprocess.run([*command, str(draft)])
        if result.returncode: raise Error(f'Editor exited with code {result.returncode}')
        if not draft.is_file(): raise Error('The editor removed the instruction draft')
        result = write(manager, read(draft), target, expected_revision=row['revision'])
    except BaseException:
        print(f'Instruction draft retained at {draft}', file=sys.stderr)
        raise
    draft.unlink()
    return result


def command(manager, args, forwarded):
    if forwarded: raise Error('Instruction editing does not accept forwarded provider arguments')
    if args.instructions_action == 'edit':
        row = edit(manager, args.profile, expected_revision=getattr(args, 'expected_revision', None))
        if row is None: return 0
        if not args.json:
            print(f'Saved instructions: {row["path"]}')
            return 0
    else:
        from agent_manager.providers.native_instructions import inspect
        row = inspect(manager, args.profile) if args.instructions_action == 'show' else describe(manager, args.profile)
    if args.json: print(json.dumps(row, indent=2))
    elif args.instructions_action == 'path': print(row['path'])
    elif row['exists']:
        if row['override']: print('Using AGENTS.override.md (takes precedence over AGENTS.md).', file=sys.stderr)
        print(row['content'], end='')
    else:
        print(f'No instruction file at {row["path"]}. Run agent {manager.provider} config instructions edit' + (f' --profile {args.profile}' if args.profile else '') + '.')
    return 0
