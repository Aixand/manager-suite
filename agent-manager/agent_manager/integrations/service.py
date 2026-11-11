"""Environment/project scoped observations of native connections and allowances."""
import copy
import json
from pathlib import Path
import shutil
import time

from agent_manager.errors import Error
from agent_manager.providers import discovery_cache as cache
from agent_manager.models.service import invalidate as invalidate_models
from agent_manager.models.schema import MAX_CACHE, ModelError, number
from agent_manager.storage.locks import locks
from agent_manager.services import schema as service_schema
from agent_manager.services import loader as service_loader
from . import opencode, readers, schema
from .policy import refresh as refresh_policy
from .dependencies import codex as codex_dependency


def invalidate(manager, environment_id):
    directory = manager.root / 'integrations' / environment_id
    if directory.is_symlink(): directory.unlink()
    elif directory.exists(): shutil.rmtree(directory)


def _policy(manager):
    try: return refresh_policy(manager.provider_spec.integrations, config=manager.config_path)
    except ValueError as error: raise Error(str(error)) from None


def context(manager, target, cwd, *, policy=None):
    policy = policy or _policy(manager)
    dependency = codex_dependency(manager, policy)
    inputs = []
    if dependency and dependency['executable']: inputs.append(Path(dependency['executable']))
    return cache.identity(manager, target, cwd, policy=policy, dependencies=dependency,
        namespace='integrations', schema=schema.SCHEMA,
        inputs=[Path(__file__), Path(opencode.__file__), Path(schema.__file__), Path(readers.__file__),
            Path(service_schema.__file__), Path(service_loader.__file__),
            Path(__file__).with_name('policy.py'), Path(__file__).with_name('dependencies.py'),
            *inputs, *sorted(opencode.NATIVE.glob('*.js')), opencode.NATIVE / 'package.json'])


def _read(path, revision, now):
    try:
        if path.is_symlink() or path.stat().st_size > MAX_CACHE: return None
        value = json.loads(path.read_text())
        if set(value) != {'schema', 'revision', 'observed_at', 'checked_at', 'failure_code', 'version', 'data'}:
            return None
        if value['schema'] != schema.SCHEMA or value['revision'] != revision: return None
        observed, checked = number(value['observed_at']), number(value['checked_at'])
        if checked is None or checked > now or observed is not None and not observed <= checked: return None
        if value['failure_code'] is not None and value['failure_code'] not in schema.IntegrationError.MESSAGES: return None
        if value['version'] is not None and (not isinstance(value['version'], str) or len(value['version']) > 32): return None
        schema.validate_data(value['data'])
        if any(row['budget']['observed_at'] is not None and row['budget']['observed_at'] > checked
               for row in value['data']['integrations']): return None
        if observed is None and value['data']['integrations']: return None
        return value
    except (OSError, ValueError, TypeError, KeyError, ModelError): return None


def _identity(data):
    return [(row['id'], row['active_connection']) for row in data['integrations']]


def _result(manager, target, directory, revision, value, now, cached):
    policy = manager.provider_spec.integrations
    data = copy.deepcopy(value['data']) if value else dict(integrations=[], routes=[], default_model=None, bridge_available=False)
    observed = value['observed_at'] if value else None
    checked = value['checked_at'] if value else None
    error = value['failure_code'] if value else None
    stale = observed is not None and (error is not None or now >= observed + policy.cache_ttl)
    if stale:
        for row in data['integrations']:
            if row['budget']['buckets']:
                row['budget']['status'] = 'stale'
                for bucket in row['budget']['buckets']: bucket['status'] = 'stale'
    for row in data['integrations']:
        for bucket in row['budget']['buckets']:
            if bucket['resets_at'] is not None and bucket['resets_at'] <= now:
                bucket['status'] = 'stale'
                row['budget']['status'] = 'stale'
    state = manager.state()
    return dict(schema=schema.SCHEMA, provider=manager.provider, environment_id=target.environment_id,
        profiles=sorted(name for name, row in state['profiles'].items() if row['environment_id'] == target.environment_id),
        cwd=str(directory), source=policy.adapter, source_revision=revision, cli_version=value['version'] if value else None,
        observed_at=observed, checked_at=checked, inspected_at=now,
        expires_at=observed + policy.cache_ttl if observed is not None else None,
        cached=cached, status='stale' if stale else 'ok' if observed is not None else 'unsupported'
            if policy.adapter == 'disabled' or error == 'unsupported' else 'unavailable' if error else 'unknown',
        error=dict(code=error, message=str(schema.IntegrationError(error))) if error else None, **data)


def get_integrations(manager, profile=None, *, cwd=None, refresh=False, cached=False):
    if any(type(flag) is not bool for flag in (refresh, cached)) or refresh and cached:
        raise Error('Choose either a native refresh or cached-only integration inspection')
    directory = Path(cwd if cwd is not None else Path.cwd()).expanduser().resolve()
    if not directory.is_dir(): raise Error(f'Working directory does not exist: {directory}')
    target = manager.resolve_profile(profile)
    policy = _policy(manager)
    if not cached and policy.adapter != 'disabled':
        # Model lists retain their independent daily gate. Lease ordering stays
        # outside the integration/environment transaction.
        try: manager.models(target, cwd=directory)
        except (Error, OSError): pass
    key = cache.digest(dict(environment=target.environment_id, cwd=str(directory)))
    with locks(manager.lock_root, 'integrations-' + key, 'environment-' + target.environment_id, wait=True):
        home, revision, path = context(manager, target, directory, policy=policy)
        now = time.time()
        previous = _read(path, revision, now)
        reset_passed = previous and any(bucket['resets_at'] is not None and bucket['resets_at'] <= now
            for row in previous['data']['integrations'] for bucket in row['budget']['buckets'])
        reusable = previous and now - previous['checked_at'] < policy.cache_ttl and (previous['failure_code'] or not reset_passed)
        if cached or policy.adapter == 'disabled' or reusable and not refresh:
            result = _result(manager, target, directory, revision, previous, now, True)
        else:
            try:
                native, bridge, version = opencode.read(manager, home, directory, policy)
                observed = time.time()
                data = schema.validate_data(schema.normalize(native, bridge, policy, observed))
                value = dict(schema=schema.SCHEMA, revision=revision, observed_at=observed,
                    checked_at=observed, failure_code=None, version=version, data=data)
                if len(json.dumps(value, allow_nan=False).encode()) > MAX_CACHE: raise ModelError('invalid-response')
                if (previous and previous['data']['bridge_available'] and data['bridge_available']
                        and _identity(previous['data']) != _identity(data)):
                    invalidate_models(manager, target.environment_id)
            except (KeyError, ValueError, TypeError):
                error = ModelError('invalid-response')
                value = dict(schema=schema.SCHEMA, revision=revision, observed_at=None, checked_at=time.time(),
                    failure_code=error.code, version=None,
                    data=dict(integrations=[], routes=[], default_model=None, bridge_available=False))
            except ModelError as error:
                value = copy.deepcopy(previous) if previous and error.code in ('unavailable', 'timeout') else dict(
                    schema=schema.SCHEMA, revision=revision, observed_at=None, version=None,
                    data=dict(integrations=[], routes=[], default_model=None, bridge_available=False))
                value.update(checked_at=time.time(), failure_code=error.code)
            if context(manager, target, directory)[1] != revision:
                raise Error('The integration discovery source changed during inspection. Refresh again.')
            cache.write(path, value)
            result = _result(manager, target, directory, revision, value, time.time(), False)
    models = manager.models(target, cwd=directory, cached=True)
    result['models_observed_at'] = models['observed_at']
    result['models_status'] = models['status']
    for row in result['routes']:
        row['models'] = [item['id'] for item in models['models'] if item['provider_id'] == row['provider_id']]
    return result


def display(snapshot):
    print(f"{snapshot['provider']}: {snapshot['status']} ({snapshot['source']})")
    for row in snapshot['integrations']:
        connection = row['active_connection']
        mode = connection['method'] if connection else 'active connection unknown'
        service = row['service_id'] or 'unrecognized'
        providers = ','.join(row['cli_providers']) or 'none'
        print(f"{row['id']}\tservice={service}\tCLI={providers}\t{mode}\t{row['budget_kind']}\t{row['budget']['status']}\t{readers.REASONS[row['budget']['reason']]}")
    if snapshot['error']: print(snapshot['error']['message'])
