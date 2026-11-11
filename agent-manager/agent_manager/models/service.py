"""One daily, bounded model snapshot per environment/project/source revision."""
import json
from pathlib import Path
import shutil
import time

from agent_manager.errors import Error
from agent_manager.storage.locks import locks, shared_environment_keys
from agent_manager.providers.discovery_cache import digest, identity, write as _write
from . import adapters
from .schema import SCHEMA, MAX_CACHE, ModelError, validate_models, zero_price, number


def invalidate(manager, environment_id):
    directory = manager.root / 'models' / environment_id
    if directory.is_symlink():
        directory.unlink()
    elif directory.exists():
        shutil.rmtree(directory)


def context(manager, target, cwd):
    return identity(manager, target, cwd, policy=manager.provider_spec.models.discovery,
        namespace='models', schema=SCHEMA, inputs=[Path(__file__), Path(adapters.__file__),
            Path(__file__).with_name('schema.py')])


def _read(path, revision, now):
    try:
        if path.is_symlink() or path.stat().st_size > MAX_CACHE: return None
        row = json.loads(path.read_text())
        if set(row) != {'schema', 'revision', 'observed_at', 'checked_at', 'models', 'source', 'version', 'failure_code'}:
            return None
        if row['schema'] != SCHEMA or row['revision'] != revision: return None
        observed, checked = number(row['observed_at']), number(row['checked_at'])
        if checked is None or checked > now or observed is not None and not observed <= checked: return None
        if row['failure_code'] is not None and row['failure_code'] not in ModelError.MESSAGES: return None
        if observed is None and row['failure_code'] is None: return None
        if row['source'] not in (*adapters.PROVIDERS, 'claude-aliases', 'claude-initialize'): return None
        if row['version'] is not None and (not isinstance(row['version'], str) or len(row['version']) > 32): return None
        row['models'] = validate_models(row['models'])
        if observed is None and row['models']: return None
        return row
    except (OSError, ValueError, TypeError, KeyError, ModelError):
        return None


def _result(manager, target, cwd, revision, observation, *, now, cached):
    policy = manager.provider_spec.models.discovery
    observed = observation['observed_at'] if observation else None
    checked = observation['checked_at'] if observation else None
    code = observation['failure_code'] if observation else None
    expired = observed is not None and now >= observed + policy.cache_ttl
    status = ('stale' if expired or code else 'ok') if observed is not None else (
        'unsupported' if policy.adapter == 'disabled' or code == 'unsupported' else 'unavailable' if code else 'unknown')
    return dict(schema=SCHEMA, provider=manager.provider, environment_id=target.environment_id,
        profiles=sorted(name for name, row in manager.state()['profiles'].items() if row['environment_id'] == target.environment_id),
        cwd=str(cwd), source=observation['source'] if observation else policy.adapter,
        adapter_revision=adapters.REVISION, cli_version=observation['version'] if observation else None,
        source_revision=revision, observed_at=observed, checked_at=checked, inspected_at=now,
        expires_at=observed + policy.cache_ttl if observed is not None else None,
        next_check_at=checked + policy.cache_ttl if checked is not None else None,
        refresh_due=policy.adapter != 'disabled' and (checked is None or now >= checked + policy.cache_ttl),
        cached=cached, status=status, models=observation['models'] if observation else [],
        error=dict(code=code, message=str(ModelError(code))) if code else None)


def get_models(manager, profile=None, *, cwd=None, refresh=False, cached=False, free=False):
    if any(type(flag) is not bool for flag in (refresh, cached, free)) or refresh and cached:
        raise Error('Choose either a native refresh or cached-only model inspection')
    directory = Path(cwd if cwd is not None else Path.cwd()).expanduser().resolve()
    if not directory.is_dir(): raise Error(f'Working directory does not exist: {directory}')
    target = manager.resolve_profile(profile)
    policy = manager.provider_spec.models.discovery
    key = digest(dict(environment=target.environment_id, cwd=str(directory)))
    with locks(manager.lock_root, 'models-' + key, 'environment-' + target.environment_id, wait=True,
               shared_keys=shared_environment_keys(manager, target.environment_id)):
        home, revision, path = context(manager, target, directory)
        now = time.time()
        previous = _read(path, revision, now)
        if cached or policy.adapter == 'disabled' or previous and now - previous['checked_at'] < policy.cache_ttl and not refresh:
            result = _result(manager, target, directory, revision, previous, now=now, cached=True)
        else:
            try:
                rows, source, version = adapters.read(manager, home, directory, policy)
                observed = time.time()
                row = dict(schema=SCHEMA, revision=revision, observed_at=observed, checked_at=observed,
                    models=sorted(validate_models(rows), key=lambda item: item['id']), source=source, version=version, failure_code=None)
                # Enforce the size limit before accepting this as a success.
                if len(json.dumps(row, allow_nan=False).encode()) > MAX_CACHE: raise ModelError('invalid-response')
            except ModelError as error:
                temporary = error.code in ('timeout', 'unavailable')
                row = previous.copy() if temporary and previous else dict(schema=SCHEMA, revision=revision,
                    observed_at=None, models=[], source=policy.adapter, version=None)
                row.update(checked_at=time.time(), failure_code=error.code)
            if context(manager, target, directory)[1] != revision:
                raise Error('The model discovery source changed during inspection. Refresh again.')
            _write(path, row)
            result = _result(manager, target, directory, revision, row, now=time.time(), cached=False)
    if free: result['models'] = [row for row in result['models'] if row['availability'] == 'listed' and zero_price(row)]
    return result


def display(snapshot):
    print(f"{snapshot['provider']}: {snapshot['status']} ({snapshot['source']})")
    for row in snapshot['models']:
        print(f"{row['id']}\t{row['name']}\t{row['availability']}")
    if snapshot['error']: print(snapshot['error']['message'])
