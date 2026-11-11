"""Generate/check the provider policy distributed in independent Manager wheels."""

import argparse
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
DESTINATIONS = [
    ROOT / "agent-manager/agent_manager/providers",
    ROOT / "conversation-manager/src/conversation_manager",
]


def synchronize(*, check=False):
    canonical = ROOT / "provider-policy"
    lock_stamp = hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
    if check:
        if (canonical / "sdk-lock.sha256").read_text().strip() != lock_stamp:
            raise ValueError(
                "SDK lock inputs changed; regenerate provider policy assets."
            )
    else:
        (canonical / "sdk-lock.sha256").write_text(lock_stamp + "\n")
    data = tomllib.loads((canonical / "manifest.toml").read_text())
    module_spec = importlib.util.spec_from_file_location(
        "canonical_provider_policy", canonical / "policy.py"
    )
    policy = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(policy)
    project_inputs = tomllib.loads((ROOT / "pyproject.toml").read_text())
    for provider, row in data["providers"].items():
        for kind in ("cli", "sdk"):
            if kind not in row:
                continue
            settings = row[kind]
            pin = settings["install_version"]
            if (
                not policy.in_range(pin, settings["supported_range"])
                or pin not in settings["tested_versions"]
                or pin in settings.get("blocked_versions", [])
            ):
                raise ValueError(
                    f"The recommended {provider} {kind} version must be tested and supported."
                )
            if (
                kind == "sdk"
                and settings["package"] + "==" + pin
                not in project_inputs["dependency-groups"]["sdk-" + provider]
            ):
                raise ValueError(
                    "SDK export inputs differ from provider policy: " + provider
                )
    assets = {
        "provider_policy.py": (canonical / "policy.py").read_bytes(),
        "provider-policy.toml": (canonical / "manifest.toml").read_bytes(),
    }
    for provider, row in data["providers"].items():
        if "sdk" in row:
            name = row["sdk"]["requirements"]
            if not check:
                result = subprocess.run(
                    [
                        "uv",
                        "export",
                        "--project",
                        str(ROOT),
                        "--frozen",
                        "--only-group",
                        "sdk-" + provider,
                        "--no-default-groups",
                        "--no-emit-project",
                        "--no-editable",
                        "--no-header",
                        "--no-annotate",
                    ],
                    check=True,
                    capture_output=True,
                )
                (canonical / name).write_bytes(result.stdout)
            assets[name] = (canonical / name).read_bytes()
    for destination in DESTINATIONS:
        for name, content in assets.items():
            if destination.name == "providers":
                # Agent Manager installs native CLIs only. Conversation SDK pins
                # belong to Conversation Manager and must not enter its distribution.
                if name == "provider_policy.py" or name.startswith("provider-sdk-"):
                    continue
                if name == "provider-policy.toml":
                    lines, skip = [], False
                    for line in content.decode().splitlines(keepends=True):
                        if line.startswith("["):
                            skip = line.startswith("[backends.") or (
                                line.startswith("[providers.")
                                and line.rstrip().endswith(".sdk]")
                            )
                        if not skip:
                            lines.append(line)
                    content = "".join(lines).encode()
            filename = (
                "policy.py"
                if destination.name == "providers" and name == "provider_policy.py"
                else name
            )
            target = destination / filename
            if check:
                if not target.is_file() or target.read_bytes() != content:
                    raise ValueError(
                        f"{target.relative_to(ROOT)} differs from the canonical provider policy; run scripts/provider_policy.py."
                    )
            else:
                target.write_bytes(content)
    # Package metadata remains static for standard Python builders. Refuse drift.
    project = tomllib.loads((ROOT / "conversation-manager/pyproject.toml").read_text())
    for provider, row in data["providers"].items():
        if "sdk" in row:
            sdk = row["sdk"]
            if (
                sdk["package"] + "==" + sdk["install_version"]
                not in project["project"]["optional-dependencies"][sdk["backend"]]
            ):
                raise ValueError(
                    "Conversation Manager SDK metadata differs from provider policy: "
                    + provider
                )
    return hashlib.sha256(assets["provider-policy.toml"]).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        print(synchronize(check=args.check))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
