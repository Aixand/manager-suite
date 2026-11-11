"""Implemented native startup paths, independent of account readiness."""
import sys


def native_startup_support(provider):
    supported = provider in ('codex', 'opencode') and sys.platform in ('linux', 'darwin', 'win32')
    return dict(supported=supported, platform=sys.platform,
                transport='stdio' if provider == 'codex' and supported else 'private-http' if supported else None,
                scope='local' if supported else None,
                verification='not-verified-with-account',
                reason=None if supported else 'No implemented native conversation startup for this provider/platform')
