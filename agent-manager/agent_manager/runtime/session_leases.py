"""Native session leases and guarded cleanup of old resource overlays."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

from agent_manager.storage.files import atomic, private_dir
from agent_manager.storage.locks import locks
from agent_manager.validation import name
from agent_manager.errors import ResourceError
from agent_manager.storage.json import read

PROVIDERS = frozenset(('codex', 'opencode', 'claude', 'copilot', 'antigravity'))



def directory(manager, launch_id):
    path = manager.root / 'terminal-resources' / name(launch_id)
    private_dir(path)
    return path


def acquire(manager, launch_id, cwd, *, exclusive=False, scope="workspace"):
    """A shared workspace overlay must not leak into another managed terminal."""
    folder = directory(manager, launch_id)
    workspace = str(Path(cwd).resolve())
    root = Path(tempfile.gettempdir()) / ('agent-manager-terminal-' + str(os.getuid() if hasattr(os, 'getuid') else 'local'))
    private_dir(root)
    key = hashlib.sha256((scope + ":" + workspace).encode()).hexdigest()
    path = root / (key + '.json')
    owner = str(folder.resolve())
    with locks(root, key, wait=True):
        entries = read(path) if path.exists() else {}
        other = {k: v for k, v in entries.items() if k != owner}
        if other and (exclusive or any(v['exclusive'] for v in other.values())):
            raise ResourceError('busy', 'This ' + scope + ' has an active session with shared resources. Stop it or choose another ' + ('profile' if scope == 'profile home' else 'directory') + '.')
        journal = folder / 'workspace.json'
        if not journal.exists():
            atomic(journal, json.dumps({'lease': str(path), 'owner': owner, 'files': [], 'mcp': None, 'additional_leases': []}).encode())
        elif read(journal)['lease'] != str(path):
            row = read(journal)
            if str(path) not in row.setdefault('additional_leases', []):
                row['additional_leases'].append(str(path))
                atomic(journal, json.dumps(row).encode())
        entries[owner] = {'exclusive': exclusive, 'workspace': workspace}
        atomic(path, json.dumps(entries).encode())


def release(manager, launch_id):
    journal = manager.root / 'terminal-resources' / name(launch_id) / 'workspace.json'
    if not journal.exists():
        return
    row = read(journal)
    lease = Path(row['lease'])
    with locks(lease.parent, lease.stem, wait=True):
        for entry in row['files']:
            path = Path(entry['path'])
            if not path.is_symlink() and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == entry['digest']:
                path.unlink()
        if row['mcp']:
            entry = row['mcp']; path = Path(entry['path'])
            if not path.is_symlink() and path.exists():
                current = read(path)
                for key, value in entry['added'].items():
                    if current.get('mcpServers', {}).get(key) == value:
                        del current['mcpServers'][key]
                if current == {'mcpServers': {}} and not entry['existed']:
                    path.unlink()
                else:
                    atomic(path, json.dumps(current, indent=2).encode())
        for directory_path in sorted(set(row.get('directories', [])), key=lambda value: len(Path(value).parts), reverse=True):
            try:
                Path(directory_path).rmdir()
            except (FileNotFoundError, OSError):
                pass  # Preserve directories containing user changes.
        entries = read(lease) if lease.exists() else {}
        entries.pop(row['owner'], None)
        atomic(lease, json.dumps(entries).encode())
    for extra in row.get('additional_leases', []):
        path = Path(extra)
        with locks(path.parent, path.stem, wait=True):
            entries = read(path) if path.exists() else {}
            entries.pop(row['owner'], None)
            atomic(path, json.dumps(entries).encode())
    for private_file in row.get('private_files', []):
        path = Path(private_file)
        if path.parent == journal.parent:
            path.unlink(missing_ok=True)
    journal.unlink()

