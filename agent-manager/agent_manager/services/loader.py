"""Overlay local service definitions on the shipped discovery catalog."""
from pathlib import Path
import tomllib

from agent_manager.providers.paths import configuration_path
from .schema import fail, parse

DEFINITIONS = Path(__file__).with_name('definitions')


def _read(directory, *, optional=False):
    if optional and not directory.exists(): return {}
    try:
        if not directory.is_dir(): fail(directory, 'service definitions must be a directory')
        result = {}
        for path in sorted(directory.glob('*.toml')):
            if path.stat().st_size > 65536: fail(path, 'service manifest exceeds 64 KiB')
            try: value = tomllib.loads(path.read_text(encoding='utf-8'))
            except (ValueError, UnicodeError): fail(path, 'invalid TOML service manifest')
            row = parse(value, source=path)
            if row.id in result: fail(path, 'duplicate service ID')
            result[row.id] = row
            if len(result) > 128: fail(directory, 'too many service definitions')
        return result
    except OSError: fail(directory, 'could not read service manifests')


def load_services(*, config=None):
    result = _read(DEFINITIONS) | _read(configuration_path(config).parent / 'services', optional=True)
    if len(result) > 128: fail('services', 'too many service definitions')
    integrations = set()
    for row in result.values():
        for key in row.discovery.integration_ids if row.discovery else ():
            if key in integrations: fail(row.source, 'ambiguous native integration ID')
            integrations.add(key)
    return dict(sorted(result.items()))
