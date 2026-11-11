"""Native CLI launch construction and Windows transport policy."""
from __future__ import annotations


def terminal_launch(manager, arguments, variables, *, managed=False):
    """Finalize provider terminal arguments and their process-local settings."""
    if managed and manager.provider == 'codex':
        from agent_manager.errors import Error
        arguments = list(arguments)
        # A managed terminal must own its backend lifetime. A shared/remote
        # daemon can keep working and holding native writer locks after stop.
        options = arguments[:arguments.index('--')] if '--' in arguments else arguments
        if any(item == '--remote' or item.startswith('--remote=') for item in options):
            raise Error('Managed Codex execution requires a local owned backend; remote attachment is unsupported')
        if '--no-daemon' not in options:
            arguments.insert(0, '--no-daemon')
    if manager.provider_spec.home.opencode:
        from agent_manager.providers.opencode import terminal_launch as opencode_launch
        return opencode_launch(arguments, variables)
    return list(arguments), variables

