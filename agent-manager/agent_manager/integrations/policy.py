"""Compile shared service definitions for a native discovery adapter."""
from dataclasses import asdict, dataclass

from agent_manager.services.schema import Authentication, fail

ADAPTER = 'opencode-integrations'


@dataclass(frozen=True)
class IntegrationRule:
    id: str
    service_id: str
    packages: tuple[str, ...]
    endpoints: tuple[str, ...]
    authentication: tuple[Authentication, ...]
    cli_providers: tuple[str, ...] = ()

    def authentication_for(self, method):
        return next((row for row in self.authentication if row.method == method), None)

    def native(self):
        return dict(id=self.id, service_id=self.service_id, packages=list(self.packages),
            endpoints=list(self.endpoints), authentication=[asdict(row) for row in self.authentication])


@dataclass(frozen=True)
class IntegrationPolicy:
    adapter: str = 'disabled'
    versions: tuple[str, ...] = ()
    timeout: int = 30
    cache_ttl: int = 300
    rules: tuple[IntegrationRule, ...] = ()
    service_ids: tuple[str, ...] | None = None

    def capabilities(self):
        return dict(supported=self.adapter != 'disabled', adapter=self.adapter,
            versions=list(self.versions), cacheTtl=self.cache_ttl,
            services=[dict(**row.native(), cli_providers=list(row.cli_providers)) for row in self.rules],
            verification='fixture-tested' if self.adapter != 'disabled' else 'unsupported')


def resolve(value, *, services=None, definitions=None):
    if services is None:
        from agent_manager.services.loader import load_services
        services = load_services()
    selected = value.get('services', sorted(services)) if value.get('adapter', 'disabled') != 'disabled' else ()
    rules = []
    for key in selected:
        if key not in services: fail('terminal.integrations.services', 'unknown service reference')
        service = services[key]
        if not service.discovery: continue
        providers = tuple(sorted(row.id for row in (definitions or {}).values() if row.service == key))
        rules.extend(IntegrationRule(native_id, key, service.discovery.packages, service.discovery.endpoints,
            service.authentication, providers) for native_id in service.discovery.integration_ids)
    if len(rules) > 128: fail('terminal.integrations.services', 'too many native integration rules')
    return IntegrationPolicy(value.get('adapter', 'disabled'), tuple(value.get('versions', ())),
        value.get('timeout', 30), value.get('cache_ttl', 300),
        tuple(sorted(rules, key=lambda row: row.id)),
        tuple(value['services']) if 'services' in value else None)


def refresh(policy, *, config=None):
    """Recompile editable service rules for a long-lived Manager without native IO."""
    from agent_manager.providers.loader import load_definitions
    from agent_manager.services.loader import load_services
    value = dict(adapter=policy.adapter, versions=policy.versions,
        timeout=policy.timeout, cache_ttl=policy.cache_ttl)
    if policy.service_ids is not None: value['services'] = policy.service_ids
    return resolve(value, services=load_services(config=config), definitions=load_definitions(config=config))
