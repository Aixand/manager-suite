"""Run a copied package without site packages, repository siblings, or network."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class StandaloneTests(unittest.TestCase):
    def test_isolated_lifecycle_and_cli_without_external_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(
                ROOT / "src/prompt_manager",
                root / "prompt_manager",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            script = r"""
import json
import runpy
import shutil
import sys
from pathlib import Path

def audit(event, args):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise AssertionError("Standalone operations must not access the network")
sys.addaudithook(audit)
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from prompt_manager import Manager, PromptError, PackageReference, SDK_CONTRACT_VERSION
from prompt_manager.codec import digest
m = Manager(root / "store", packs=[])
assert not m.catalog()["managed"]
assert m.check_store()["ok"]
assert not m.store.home.exists()
created = m.create_managed("review", content="Original.\n")
assert SDK_CONTRACT_VERSION == 1
handle = m.package("local", "review")
reference = PackageReference.from_dict(json.loads(json.dumps(handle.reference.to_dict())))
assert m.open_package(reference).resolve()["digest"] == created["active_digest"]
assert handle.validate({"body.md": "Candidate."})["valid"]
tagged = handle.set_tags([" Review "], expected_revision=handle.status()["revision"])
assert tagged["tags"] == ["review"]
found = m.search_managed("review", tags=["review"], publisher_id="local")
assert found["complete"] and found["items"][0]["package_id"] == "review"

backup_path = root / "standalone-backup.zip"
backed_up = m.backup_store(backup_path)
restored = Manager(root / "restored-store", packs=[])
restored_result = restored.restore_store(backup_path)
assert backed_up["digest"] == restored_result["digest"]
assert restored.package("local", "review").render("review")["output"] == "[user]\nOriginal.\n"
copy = handle.duplicate("independent", label="Personal copy")
copy_handle = m.package("local", copy["id"])
archived = copy_handle.archive(expected_revision=copy["revision"])
assert archived["archived"]
assert not any(row.get("id") == "independent" for row in m.managed_summaries())
assert copy_handle.render("review")["output"] == "[user]\nOriginal.\n"
copy_handle.unarchive(expected_revision=archived["revision"])


handle.export(root / "isolated-export.json")

snapshot = m.resolve_managed("local", "review")["snapshot"]
source = root / "publisher"
source.mkdir()
(source / "pack.json").write_text(json.dumps(snapshot["pack"]))
(source / "body.md").write_text(snapshot["files"]["body.md"])
registered = m.package("publisher").register(source, publisher_revision=1)
assert registered.content == "[user]\nOriginal.\n"
assert registered.to_dict()["schema"] == "prompt-manager/registration-result-1"
initial = registered.status
source_descriptor = m.register_source("authoring", [source], expected_revision=None)
assert m.source_descriptor("authoring") == source_descriptor
application = m.register_application("workspace", prompts={"review": {
    "kind": "managed", "publisher_id": "publisher", "package_id": "default", "prompt": "review",
}})
assert m.application_descriptor("workspace") == application
assert m.render_application("workspace", "review")["resolution"]["target"]["kind"] == "managed"

custom = m.save_managed("publisher", "default", {"body.md": "My version.\n"},
                        expected_revision=initial["revision"])
(source / "body.md").write_text("Publisher version 2.\n")
pending = m.ensure_registered("publisher", source, publisher_revision=2)
assert pending["reconciliation_required"]
assert m.render_managed("publisher", "default", "review")["output"] == "[user]\nMy version.\n"
preview = m.preview_reconciliation("publisher", "default")
assert preview["conflicts"]
kept = m.reconcile("publisher", "default", expected_revision=preview["revision"], take="managed")
assert not kept["reconciliation_required"]
try:
    m.save_managed("publisher", "default", {"body.md": "Stale."},
                   expected_revision=custom["revision"])
except PromptError as error:
    assert error.code == "conflict"
else:
    raise AssertionError("Stale edit accepted")
m.release_managed("publisher", "default", "1")
shutil.rmtree(source)
fallback = m.package("publisher").register(source, publisher_revision=3)
assert fallback.status["outcome"] == "refresh_failed"
assert fallback.content == "[user]\nMy version.\n"
assert m.render("review@1/review")["output"] == "[user]\nMy version.\n"
assert m.render_managed("publisher", "default", "review",
                        revision=initial["revision"])["output"] == "[user]\nOriginal.\n"
# Legacy remote provenance is retained as inert metadata, without importing adapters.
record = root / "store/managed/publisher/default.json"
row = json.loads(record.read_text())
row["source_review"] = True
row["source"] = {"schema": "resource-sources/github-1", "url": "https://example.invalid"}
row["revision"] = digest({k: v for k, v in row.items() if k != "revision"})
record.write_text(json.dumps(row))
before = record.read_bytes()
assert m.render_managed("publisher", "default", "review")["output"] == "[user]\nMy version.\n"
assert record.read_bytes() == before
legacy = m.managed_status("publisher", "default")
assert legacy["reconciliation_required"]
review = m.preview_reconciliation("publisher", "default")
acknowledged = m.reconcile("publisher", "default", expected_revision=review["revision"], take="managed")
assert not acknowledged["reconciliation_required"]
assert acknowledged["source"] == row["source"]
assert "resource_sources" not in sys.modules
sys.argv = ["prompt-manager", "--home", str(root / "store"), "managed", "create",
            "cli-prompt", "--content", "Created in isolation."]
runpy.run_module("prompt_manager", run_name="__main__")
"""
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-c", script, str(root)],
                cwd=root,
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("cli-prompt", json.loads(result.stdout)["id"])
