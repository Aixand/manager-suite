"""Public model metadata. Provider settings, headers and credentials are excluded."""
import math
import re
from agent_manager.providers.discovery_cache import MAX_CACHE

SCHEMA = 'agent-manager/models-1'
MAX_MODELS = 2048


class ModelError(Exception):
    MESSAGES = {
        'authentication': 'Check sign-in using the native provider CLI.',
        'timeout': 'Model discovery timed out. Refresh to retry.',
        'unavailable': 'The native model catalog is unavailable. Refresh to retry.',
        'unsupported': 'This CLI version has no qualified model discovery adapter.',
        'invalid-response': 'The provider returned an unrecognized model catalog.',
        'missing-cli': 'The provider executable is unavailable.',
    }

    def __init__(self, code):
        self.code = code
        super().__init__(self.MESSAGES[code])


def text(value, *, optional=False, maximum=256):
    if value is None and optional: return None
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ModelError('invalid-response')
    return value


def selector(value):
    value = text(value)
    if re.search(r'\s', value): raise ModelError('invalid-response')
    return value


def number(value):
    if value is None: return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ModelError('invalid-response')
    return value


def boolean(value):
    if value is not None and type(value) is not bool: raise ModelError('invalid-response')
    return value


def model(key, name=None, *, description=None, provider_id=None, kind='model', resolved_id=None,
          availability='listed', capabilities=None, limits=None, variants=None, pricing=None, default=None):
    if kind not in ('model', 'alias', 'preset') or availability not in ('listed', 'restricted', 'unknown'):
        raise ModelError('invalid-response')
    if capabilities is not None and (not isinstance(capabilities, dict) or capabilities.keys() - {'tools', 'vision', 'reasoning'}):
        raise ModelError('invalid-response')
    capabilities = {key: boolean((capabilities or {}).get(key)) for key in ('tools', 'vision', 'reasoning')}
    if limits is not None and (not isinstance(limits, dict) or limits.keys() - {'context_tokens', 'output_tokens'}):
        raise ModelError('invalid-response')
    limits = {key: number((limits or {}).get(key)) for key in ('context_tokens', 'output_tokens')}
    if variants is not None:
        if not isinstance(variants, list) or len(variants) > 128: raise ModelError('invalid-response')
        clean = []
        for item in variants:
            if not isinstance(item, dict) or item.keys() - {'id', 'name', 'category', 'default'}:
                raise ModelError('invalid-response')
            clean.append(dict(id=selector(item.get('id')), name=text(item.get('name'), optional=True),
                category=text(item.get('category'), optional=True), default=boolean(item.get('default'))))
        variants = clean
    if pricing is not None:
        fields = {'input', 'output', 'cache_read', 'cache_write', 'cache_write_1h',
            'context_over', 'max_prompt_tokens', 'batch_size', 'multiplier'}
        if not isinstance(pricing, list) or len(pricing) > 128: raise ModelError('invalid-response')
        clean = []
        for tier in pricing:
            if not isinstance(tier, dict) or tier.keys() - (fields | {'unit'}): raise ModelError('invalid-response')
            if tier.get('unit') not in (None, 'usd-per-million-tokens', 'premium-request-multiplier', 'ai-credits-per-batch'):
                raise ModelError('invalid-response')
            clean.append(dict(unit=tier['unit'], **{key: number(tier.get(key)) for key in sorted(fields)}))
        pricing = clean
    return dict(id=selector(key), name=text(name or key), description=text(description, optional=True, maximum=2048),
        provider_id=text(provider_id, optional=True), kind=kind,
        resolved_id=selector(resolved_id) if resolved_id is not None else None,
        availability=availability, capabilities=capabilities, limits=limits, variants=variants,
        pricing=pricing, default=boolean(default))


def validate_models(rows):
    if not isinstance(rows, list) or len(rows) > MAX_MODELS: raise ModelError('invalid-response')
    clean, seen = [], set()
    for row in rows:
        if not isinstance(row, dict): raise ModelError('invalid-response')
        try: item = model(row['id'], **{key: value for key, value in row.items() if key != 'id'})
        except (KeyError, TypeError): raise ModelError('invalid-response') from None
        if item['id'] in seen: raise ModelError('invalid-response')
        seen.add(item['id']); clean.append(item)
    return clean


def zero_price(row):
    """Advertised token prices only; this says nothing about account allowance."""
    tiers = row['pricing']
    return bool(tiers) and all(t['unit'] == 'usd-per-million-tokens' and t['input'] == t['output'] == 0
        and t['cache_read'] in (None, 0) and t['cache_write'] in (None, 0) for t in tiers)
