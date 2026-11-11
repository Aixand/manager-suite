"""Exercise the exact release archive through its Python-free bootstrap."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import zipfile


def qualify(archive):
    archive = Path(archive).resolve()
    with tempfile.TemporaryDirectory(prefix="manager-download-") as temporary:
        base = Path(temporary)
        extracted = base / "extracted"
        extracted.mkdir()
        # Only archives assembled locally by ci_release are accepted here.
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(extracted)
        else:
            with tarfile.open(archive) as bundle:
                bundle.extractall(extracted, filter="data")
        roots = list(extracted.iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            raise ValueError("Release archive must contain one root directory")
        root = roots[0]
        descriptor = json.loads((root / "product-descriptor.json").read_text())
        environment = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(
                (
                    "AGENT_",
                    "AIXAND_",
                    "CONVERSATION_",
                    "MCP_",
                    "PROMPT_",
                    "SKILL_",
                    "UC_",
                    "UNDERCURRENT_",
                    "PYTHON",
                )
            )
        }
        home = base / "home"
        home.mkdir()
        environment.update(
            HOME=str(home),
            USERPROFILE=str(home),
            XDG_DATA_HOME=str(home / "data"),
            LOCALAPPDATA=str(home / "local"),
            AIXAND_INSTALLER_CACHE=str(base / "cache"),
        )
        if os.name == "nt":
            powershell = shutil.which("powershell") or shutil.which("pwsh")
            launcher = [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "bootstrap/bootstrap.ps1"),
            ]
            # Keep the native archive tool while excluding PATH Python.
            tools = base / "tools"
            tools.mkdir()
            tar = shutil.which("tar.exe")
            if not tar:
                raise RuntimeError(
                    "Windows bootstrap qualification requires native tar.exe"
                )
            shutil.copyfile(tar, tools / "tar.exe")
            environment["PATH"] = str(tools)
        else:
            tools = base / "tools"
            tools.mkdir()
            for name in (
                "sh",
                "curl",
                "tar",
                "gzip",
                "sha256sum",
                "shasum",
                "uname",
                "mkdir",
                "mktemp",
                "mv",
                "rm",
                "rmdir",
            ):
                if executable := shutil.which(name):
                    (tools / name).symlink_to(executable)
            environment["PATH"] = str(tools)
            launcher = ["/bin/sh", str(root / "bootstrap/bootstrap.sh")]
        store = base / "software"

        def run(*arguments, installed_command=False):
            command = list(map(str, arguments))
            result = subprocess.run(
                command if installed_command else [*launcher, *command],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=900,
            )
            if result.returncode:
                raise RuntimeError(
                    f"Archive bootstrap failed: {arguments}\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}"
                )
            return result

        run("install", "--artifact", root / "artifacts", "--prefix", store)
        for product in descriptor["products"].values():
            for command in product["commands"]:
                executable = (
                    store / "bin" / (command + (".exe" if os.name == "nt" else ""))
                )
                run(executable, "--help", installed_command=True)
        run("inspect", "--prefix", store)
        run(
            "uninstall",
            "--prefix",
            store,
            "--product",
            descriptor["selection"],
            "--stopped",
        )
        with archive.open("rb") as stream:
            archive_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        result = {
            "passed": True,
            "archive_sha256": archive_sha256,
            "target": descriptor["target"],
            "manifest_sha256": descriptor["manifest_sha256"],
            "checks": [
                "final-archive-extraction",
                "bootstrap-without-path-python",
                "commands",
                "installed-command-launchers",
                "inspect",
                "uninstall",
            ],
        }
    return result
