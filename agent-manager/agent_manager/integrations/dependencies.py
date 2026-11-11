"""Resolve reader executables through provider configuration, without logins."""
from pathlib import Path
import shutil


def codex(manager, policy):
    enabled = any(rule.service_id == 'openai' and any(auth.method == 'oauth'
        and auth.budget_adapter == 'openai-native' for auth in rule.authentication) for rule in policy.rules)
    if not enabled: return None
    from agent_manager.providers.config import load_registry
    spec = load_registry(manager.config_path).resolve('codex')
    executable = shutil.which(spec.settings['executable'])
    return dict(executable=str(Path(executable).resolve()) if executable else None,
        versions=list(spec.budget.versions) if spec.service == 'openai' and spec.budget.adapter == 'codex-rate-limits' else [])
