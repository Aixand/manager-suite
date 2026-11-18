"""Provider-free profile identity walkthrough; creates only a temporary store."""
import os
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
from agent_manager import Manager


def main():
    with TemporaryDirectory(prefix="agent-profile-example-") as directory:
        base = Path(directory)
        config = base / "providers.toml"
        config.write_text("")
        with patch.dict(os.environ, {"AGENT_MANAGER_HOMES_ROOT": str(base / "homes")}):
            manager = Manager("codex", config=config, root=base / "state")
            original = manager.new_profile("work")
            manager.alias_profile("review", "work")
            manager.set_default("work")
            manager.rename("work", "company")
            target = manager.resolve_agent_target(original["environment_id"])
            assert manager.home("review") == manager.home("company")
            assert manager.resolve_profile() == "company"
            assert target["environment_id"] == original["environment_id"]
            print("Aliases share one stable environment; rename preserves the default.")
    print("Temporary store removed; no provider process or login was used.")


if __name__ == "__main__":
    main()
