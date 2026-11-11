"""Release profiles; Python API copies never imply top-level product installs."""

PYTHON = "3.12.14"
PACKAGES = {
    "local-runtime": ("aixand-lib-local-runtime", "aixand_local_runtime"),
    "filesystem": ("aixand-lib-filesystem", "aixand_filesystem"),
    "github-source": ("aixand-lib-github-source", "github_source"),
    "aixand-undercurrent-sdk": ("aixand-undercurrent-sdk", "undercurrent"),
    "mcp-manager": ("aixand-mcp-manager", "mcp_manager"),
    "prompt-manager": ("aixand-prompt-manager", "prompt_manager"),
    "skill-manager": ("aixand-skill-manager", "skill_manager"),
    "agent-manager": ("aixand-agent-manager", "agent_manager"),
    "conversation-manager": ("aixand-conversation-manager", "conversation_manager"),
}
PROFILES = {
    "mcp-manager": ["filesystem", "github-source", "mcp-manager"],
    "prompt-manager": ["prompt-manager"],
    "skill-manager": ["filesystem", "prompt-manager", "skill-manager"],
    "agent-manager": [
        "filesystem",
        "local-runtime",
        "aixand-undercurrent-sdk",
        "agent-manager",
    ],
    "conversation-manager": [
        "filesystem",
        "local-runtime",
        "agent-manager",
        "conversation-manager",
    ],
}
EXTRAS = {
    "agent-manager": ["mcp", "undercurrent"],
    "conversation-manager": ["agents", "native-codex"],
}
COMMANDS = {
    "mcp-manager": ["mcp-manager"],
    "prompt-manager": ["prompt-manager"],
    "skill-manager": ["skill-manager"],
    "agent-manager": ["agent", "agent-manager-mcp"],
    "conversation-manager": ["conversation-manager"],
}

# These distributions are built from this repository.
MANAGED_PACKAGES = {PACKAGES[name][0] for name in PROFILES}
