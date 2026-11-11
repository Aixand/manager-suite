"""Keep ambient credentials from overriding the selected native profile."""
def environment(provider, variables):
    result = dict(variables)
    competing = {
        "codex": ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_BASE_URL", "CODEX_HOME", "CODEX_SQLITE_HOME", "OPENAI_FEDERATION_RULE_ID", "OPENAI_IDENTITY_TOKEN_FILE", "OPENAI_WORKLOAD_IDENTITY_CONTEXT"),
        "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "ANTHROPIC_CUSTOM_HEADERS", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "CLAUDE_CODE_OAUTH_SCOPES", "ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID"),
        "copilot": ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "COPILOT_HOME",
            "COPILOT_PROVIDERS_CONFIG", "COPILOT_SDK_AUTH_TOKEN", "GH_HOST", "COPILOT_GH_HOST"),
        "antigravity": ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_ACCESS_TOKEN",
            "AGY_ADC_AUTH", "GOOGLE_GEMINI_BASE_URL", "GOOGLE_GENAI_USE_VERTEXAI",
            "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GOOGLE_CLOUD_QUOTA_PROJECT"),
    }
    for key in competing.get(provider, ()): result.pop(key, None)
    if provider == "copilot":
        # This namespace selects BYOK credentials, endpoints and model routing.
        # Native providers.json in the selected home remains provider-owned.
        for key in tuple(result):
            if key.startswith("COPILOT_PROVIDER_"):
                result.pop(key)
    if provider == "claude":
        import sys
        if sys.platform == "darwin":
            import os, pwd
            username = pwd.getpwuid(os.getuid()).pw_name
            result.update(USER=username, LOGNAME=username)
    return result
