"""Owned installation generations using captured local source and no registries."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid

from source_fixture import GitHubFixture
from mcp_manager import Manager


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manager = Manager(self.root / "mcp")

    def wait(self, row):
        deadline = time.monotonic() + 45
        while row["status"] in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(0.05)
            row = self.manager.source_job(row["id"])
        if row["status"] in ("queued", "running"):
            self.manager.cancel_source_job(row["id"])
        return row

    def node(self, marker, *, fail=False):
        package = {
            "name": "fixture-server",
            "version": "1.0.0",
            "bin": {"fixture-server": "./server.js"},
            **({"scripts": {"build": 'node -e "process.exit(1)"'}} if fail else {}),
        }
        lock = {
            "name": "fixture-server",
            "version": "1.0.0",
            "lockfileVersion": 3,
            "requires": True,
            "packages": {"": package},
        }
        return {
            "package.json": json.dumps(package),
            "package-lock.json": json.dumps(lock),
            "server.js": f"console.log({json.dumps(marker)})\n",
        }

    def imported(self, files):
        self.manager.source_client = GitHubFixture(files)
        discovery = self.manager.discover_source("https://github.com/example/resources")
        return self.manager.import_source(discovery["discovery_id"])["items"][0]

    def install(self, item, *, candidate="active", key=None):
        return self.wait(
            self.manager.start_source_job(
                "install",
                {
                    "publisher": item["publisher_id"],
                    "identifier": item["id"],
                    "expected_revision": item["revision"],
                    "candidate": candidate,
                },
                key=key,
            )
        )

    def invoke(self, item, revision=None):
        resolved = self.manager.inspect_managed(
            item["publisher_id"], item["id"], self.root, revision=revision
        )
        server = next(iter(resolved["servers"].values()))
        output = subprocess.check_output(
            [server["command"], *server["args"]], cwd=server["cwd"], text=True
        )
        return output.strip(), resolved

    @unittest.skipUnless(
        shutil.which("npm") and shutil.which("node"), "Node and npm are not installed"
    )
    def test_node_generation_survives_updates_failures_and_source_archive_loss(self):
        first = self.imported(self.node("first"))
        key = str(uuid.uuid4())
        job = self.install(first, key=key)
        self.assertEqual(job["status"], "completed", job)
        output, initial_resolution = self.invoke(first)
        self.assertEqual(output, "first")
        generation = initial_resolution["states"][0]["generation"]
        self.manager.source_client.change(self.node("broken", fail=True))
        pending = self.manager.check_updates(
            first["publisher_id"], first["id"], force=True
        )
        failed = self.install(pending, candidate="publisher")
        self.assertEqual(failed["status"], "failed", failed)
        self.assertEqual(self.invoke(first)[0], "first")
        self.manager.source_client.change(self.node("next"))
        pending = self.manager.check_updates(
            first["publisher_id"], first["id"], force=True
        )
        prepared = self.install(pending, candidate="publisher")
        self.assertEqual(prepared["status"], "completed", prepared)
        self.assertEqual(self.invoke(first)[0], "first")
        accepted = self.manager.reconcile(
            first["publisher_id"],
            first["id"],
            expected_revision=pending["revision"],
            take="publisher",
        )
        self.assertEqual(self.invoke(accepted)[0], "next")
        self.assertEqual(self.invoke(first, first["revision"])[0], "first")
        self.assertEqual(
            self.invoke(first, first["revision"])[1]["states"][0]["generation"],
            generation,
        )
        retry = self.manager.start_source_job(
            "install",
            {
                "publisher": first["publisher_id"],
                "identifier": first["id"],
                "expected_revision": first["revision"],
                "candidate": "active",
            },
            key=key,
        )
        self.assertEqual(retry["id"], job["id"])
        self.assertEqual(retry["result"], job["result"])
        shutil.rmtree(self.manager.home / "source-objects")
        self.assertEqual(self.invoke(accepted)[0], "next")

    def test_python_source_uses_captured_wheels_and_console_script(self):
        # A tiny PEP 517 backend with no external build/runtime dependencies.
        backend = """import pathlib, zipfile
def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
 name='fixture_server-1.0.0-py3-none-any.whl'
 files={'fixture_server.py': 'def main():\\n print("python fixture")\\n',
 'fixture_server-1.0.0.dist-info/METADATA':'Metadata-Version: 2.1\\nName: fixture-server\\nVersion: 1.0.0\\n',
 'fixture_server-1.0.0.dist-info/WHEEL':'Wheel-Version: 1.0\\nGenerator: fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n',
 'fixture_server-1.0.0.dist-info/entry_points.txt':'[console_scripts]\\nfixture-server = fixture_server:main\\n'}
 files['fixture_server-1.0.0.dist-info/RECORD']=''.join(k+',,\\n' for k in [*files,'fixture_server-1.0.0.dist-info/RECORD'])
 with zipfile.ZipFile(pathlib.Path(wheel_directory)/name,'w') as z:
  for k,v in files.items(): z.writestr(k,v)
 return name
"""
        files = {
            "pyproject.toml": '[build-system]\nrequires=[]\nbuild-backend="fixture_backend"\nbackend-path=["."]\n[project]\nname="fixture-server"\nversion="1.0.0"\n[project.scripts]\nfixture-server="fixture_server:main"\n',
            "fixture_backend.py": backend,
        }
        first = self.imported(files)
        installed = self.install(first)
        self.assertEqual(installed["status"], "completed", installed)
        self.assertEqual(self.invoke(first)[0], "python fixture")
        generation = Path(installed["result"]["runtime"]["directory"])
        self.assertTrue(list((generation / "wheels").glob("*.whl")))


if __name__ == "__main__":
    unittest.main()
