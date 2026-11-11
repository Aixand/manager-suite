"""Validated, credential-free quota buckets. Session costs are a separate scope."""
from datetime import datetime
import math
import re


SCHEMA = 'agent-manager/budget-1'
MAX_BUCKETS = 128


class BudgetError(Exception):
    """Only fixed public messages may cross the native reader boundary."""
    MESSAGES = {
        'authentication': 'Check sign-in in this environment using the native provider CLI.',
        'timeout': 'The native budget query timed out. Refresh to retry.',
        'unavailable': 'The provider could not supply account allowance. Refresh to retry.',
        'unsupported': 'This CLI version does not expose the qualified budget interface.',
        'invalid-response': 'The provider returned an unrecognized budget response.',
        'missing-cli': 'The provider executable is unavailable.',
    }

    def __init__(self, code, *, installed_version=None, supported_versions=()):
        self.code = code
        message = self.MESSAGES[code]
        if installed_version is not None:
            message += f" Installed: {installed_version}. Supported: {', '.join(supported_versions)}."
        super().__init__(message)


def failure(value):
    """Classify a provider failure without publishing its message or raw data."""
    code = value.get('code') if isinstance(value, dict) else None
    message = str(value).casefold()
    if code in (401, 403) or any(word in message for word in (
            'unauthorized', 'unauthenticated', 'not logged in', 'not signed in',
            'authentication', 'invalid token', 'expired token', 'login required')):
        return BudgetError('authentication')
    if code == -32601:
        return BudgetError('unsupported')
    return BudgetError('unavailable')


def number(value, *, maximum=None):
    if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
            or maximum is not None and value > maximum):
        raise BudgetError('invalid-response')
    return value


def text(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise BudgetError('invalid-response')
    return value


def identifier(value):
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}', value) is None:
        raise BudgetError('invalid-response')
    return value


def timestamp(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError
            value = parsed.timestamp()
        except (ValueError, OverflowError, OSError):
            raise BudgetError('invalid-response') from None
    return number(value, maximum=253402300799)


def bucket(key, label, *, unit='percent', remaining=None, used=None, limit=None,
           remaining_percent=None, resets_at=None, window_seconds=None,
           unlimited=False, disabled=False, scope=None, kind='allowance'):
    if unit not in ('percent', 'requests', 'credits', 'usd') or kind not in ('allowance', 'spending-cap'):
        raise BudgetError('invalid-response')
    if type(unlimited) is not bool or type(disabled) is not bool:
        raise BudgetError('invalid-response')
    scope = {} if scope is None else scope
    if not isinstance(scope, dict) or scope.keys() - {'model', 'surface', 'model_group', 'limit_id'}:
        raise BudgetError('invalid-response')
    scope = {identifier(k): text(v) for k, v in scope.items()}
    values = [number(v) if v is not None else None for v in (remaining, used, limit)]
    remaining, used, limit = values
    if remaining_percent is not None:
        remaining_percent = number(remaining_percent, maximum=100)
    # A reported 0 / 0 entitlement does not establish an exhausted allowance.
    if disabled:
        status = 'disabled'
    elif unlimited:
        status, remaining_percent = 'unlimited', None
    elif remaining_percent == 0 or remaining == 0 and limit != 0:
        status = 'exhausted'
    elif remaining_percent is not None or remaining is not None and remaining > 0:
        status = 'available'
    else:
        status = 'unknown'
    return dict(id=identifier(key), label=text(label), kind=kind, unit=unit,
        remaining=remaining, used=used, limit=limit, remaining_percent=remaining_percent,
        resets_at=timestamp(resets_at), window_seconds=number(window_seconds) if window_seconds is not None else None,
        unlimited=unlimited, disabled=disabled, scope=scope, status=status)


def validate_buckets(rows):
    if not isinstance(rows, list) or len(rows) > MAX_BUCKETS:
        raise BudgetError('invalid-response')
    result, keys = [], set()
    fields = set(bucket('check', 'Check')) - {'status', 'id'}
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields | {'id', 'status'}:
            raise BudgetError('invalid-response')
        current = bucket(row['id'], **{k: row[k] for k in fields})
        if current['id'] in keys:
            raise BudgetError('invalid-response')
        keys.add(current['id']); result.append(current)
    return result


def observation_status(rows):
    if not rows or all(row['status'] == 'unknown' for row in rows):
        return 'unknown'
    return 'partial' if any(row['status'] == 'unknown' for row in rows) else 'ok'
