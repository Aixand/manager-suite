"""Read bundled policy without executing a provider or contacting a service."""
import json
from importlib.resources import files
import tomllib


def main():
    root = files("agent_manager").joinpath("providers")
    general = tomllib.loads(root.joinpath("provider-policy.toml").read_text())
    terminal = tomllib.loads(root.joinpath("terminal-policy.toml").read_text())
    for provider, row in terminal["providers"].items():
        adapter = row.get("continuation")
        print(json.dumps({"provider": provider,
            "general_range": general["providers"][provider]["cli"]["supported_range"],
            "installation": row["install_version"],
            "continuation_range": adapter["supported_range"] if adapter else None,
            "verification": adapter["verification"] if adapter else None}, sort_keys=True))


if __name__ == "__main__":
    main()
