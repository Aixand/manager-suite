"""Provider aliases resolved from the manifest registry."""
from agent_manager.errors import Error
from agent_manager.providers import ManifestError, aliases


def canonical(provider, config=None):
    try:
        return aliases(config=config).get(provider, provider)
    except ManifestError as error:
        raise Error(str(error)) from None

