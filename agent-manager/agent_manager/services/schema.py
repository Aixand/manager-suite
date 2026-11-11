"""Bounded service manifests select built-in readers, never executable code."""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit

NAME = re.compile(r'[a-z0-9][a-z0-9_-]{0,127}')
METHODS = ('key', 'oauth', 'external', 'command')
KINDS = ('subscription', 'api-billing', 'credits', 'unknown')
# Protocol implementations, not discovery rules: readers cannot be repurposed
# for another upstream service or authentication type.
READERS = {'openai-native': ('openai', {'oauth': 'subscription', 'key': 'api-billing'}),
           'openrouter-key': ('openrouter', {'key': 'credits'})}


class ServiceError(ValueError):
    """A service definition is invalid or ambiguous."""


def fail(path, message):
    raise ServiceError(f'{path}: {message}')


def table(value, path, required=(), optional=()):
    if not isinstance(value, dict): fail(path, 'must be a table')
    if value.keys() - set(required) - set(optional): fail(path, 'unknown fields')
    if set(required) - value.keys(): fail(path, 'missing required fields')
    return value


def text(value, path, pattern=None):
    if not isinstance(value, str) or not value.strip() or len(value) > 512 or any(ord(c) < 32 for c in value):
        fail(path, 'requires a bounded single-line string')
    if pattern and not pattern.fullmatch(value): fail(path, 'invalid identifier')


def strings(value, path, pattern=None):
    if not isinstance(value, list) or not 1 <= len(value) <= 64: fail(path, 'requires 1–64 strings')
    for item in value: text(item, path, pattern)
    if len(set(value)) != len(value): fail(path, 'duplicate values')


@dataclass(frozen=True)
class Authentication:
    method: str
    budget_kind: str
    budget_adapter: str = 'disabled'


@dataclass(frozen=True)
class Discovery:
    integration_ids: tuple[str, ...]
    packages: tuple[str, ...]
    endpoints: tuple[str, ...]


@dataclass(frozen=True)
class ServiceDefinition:
    id: str
    label: str
    authentication: tuple[Authentication, ...]
    discovery: Discovery | None
    source: str


def parse(value, *, source='<service>'):
    root = str(source)
    table(value, root, ('schema_version', 'id', 'label'), ('authentication', 'discovery'))
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        fail(root, 'only service schema version 1 is supported')
    text(value['id'], root + '.id', NAME); text(value['label'], root + '.label')
    authentication = value.get('authentication', {})
    table(authentication, root + '.authentication', optional=METHODS)
    auth = []
    for method, row in sorted(authentication.items()):
        path = root + '.authentication.' + method
        table(row, path, ('budget_kind',), ('budget_adapter',))
        adapter = row.get('budget_adapter', 'disabled')
        if row['budget_kind'] not in KINDS: fail(path, 'unknown budget kind')
        if not isinstance(adapter, str) or adapter not in ('disabled', *READERS): fail(path, 'unknown budget reader')
        if adapter != 'disabled':
            service, methods = READERS[adapter]
            if value['id'] != service or methods.get(method) != row['budget_kind']:
                fail(path, 'budget reader does not support this service and authentication type')
        auth.append(Authentication(method, row['budget_kind'], adapter))
    discovery = None
    if 'discovery' in value:
        table(value['discovery'], root + '.discovery', ('opencode',))
        row = value['discovery']['opencode']; path = root + '.discovery.opencode'
        table(row, path, ('integration_ids', 'packages', 'endpoints'))
        strings(row['integration_ids'], path + '.integration_ids', NAME)
        strings(row['packages'], path + '.packages', re.compile(r'[@A-Za-z0-9_./-]{1,256}'))
        strings(row['endpoints'], path + '.endpoints')
        for endpoint in row['endpoints']:
            try:
                url = urlsplit(endpoint)
                valid = (url.scheme == 'https' and url.hostname and not url.username and not url.password
                    and not url.query and not url.fragment and url.port in (None, 443))
            except ValueError: valid = False
            if not valid: fail(path + '.endpoints', 'requires HTTPS endpoints without credentials, queries or fragments')
        if not auth: fail(path, 'requires declared authentication types')
        discovery = Discovery(tuple(row['integration_ids']), tuple(row['packages']),
            tuple(endpoint.rstrip('/') for endpoint in row['endpoints']))
    return ServiceDefinition(value['id'], value['label'], tuple(auth), discovery, root)
