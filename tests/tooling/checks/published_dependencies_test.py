"""Published inputs are checksum-bound; ordinary CI never needs external source trees."""

import io
import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import ci_environment
import ci_release
import published_dependencies as published


def make_wheel(name="aixand-undercurrent-sdk", version="0.1.0b0"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "fixture.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n",
        )
    return stream.getvalue()


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / ".github/ci").mkdir(parents=True)
        self.content = make_wheel()
        self.record = {
            "version": "0.1.0b0",
            "wheel": {
                "filename": "aixand_undercurrent_sdk-0.1.0b0-py3-none-any.whl",
                "url": "https://example.invalid/aixand_undercurrent_sdk-0.1.0b0-py3-none-any.whl",
                "sha256": published.sha(self.content),
            },
        }
        self.write_manifest()

    def write_manifest(self):
        (self.root / ".github/ci/dependencies.json").write_text(
            json.dumps(
                {"schema": 1, "packages": {"aixand-undercurrent-sdk": self.record}}
            )
        )

    def test_fetches_the_pinned_wheel_and_reuses_verified_cache(self):
        with patch.object(published, "download", return_value=self.content) as download:
            wheel = published.fetch("aixand-undercurrent-sdk", self.root)
            self.assertEqual(wheel.read_bytes(), self.content)
            self.assertEqual(
                published.fetch("aixand-undercurrent-sdk", self.root), wheel
            )
            self.assertEqual(download.call_count, 1)
        self.assertFalse((self.root / "not-extracted").exists())

    def test_corrupt_cache_is_rejected_without_overwriting(self):
        with patch.object(published, "download", return_value=self.content):
            wheel = published.fetch("aixand-undercurrent-sdk", self.root)
        wheel.write_bytes(b"corrupted")
        with patch.object(published, "download") as download:
            with self.assertRaisesRegex(ValueError, "checksum"):
                published.fetch("aixand-undercurrent-sdk", self.root)
            download.assert_not_called()
        self.assertEqual(wheel.read_bytes(), b"corrupted")

    def test_cache_symlinks_are_rejected(self):
        cache = self.root / "build/published-wheels"
        cache.mkdir(parents=True)
        target = self.root / "other.whl"
        target.write_bytes(self.content)
        (cache / self.record["wheel"]["filename"]).symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            published.fetch("aixand-undercurrent-sdk", self.root)

    def test_wrong_version_cannot_masquerade_as_pinned_wheel(self):
        self.record["version"] = "9.0.0"
        with self.assertRaisesRegex(ValueError, "identity"):
            published.verify_wheel(self.content, "aixand-undercurrent-sdk", self.record)

    def test_download_rejects_plain_http_and_wrong_bytes(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            published.download("http://example.invalid/wheel", "a" * 64)
        with patch.object(
            published.urllib.request, "urlopen", return_value=io.BytesIO(b"wrong")
        ):
            with self.assertRaisesRegex(ValueError, "checksum"):
                published.download("https://example.invalid/wheel", "a" * 64)

    def test_requirement_uses_exact_artifact_and_rejects_stale_lock_version(self):
        wheel = self.root / "wheel with spaces.whl"
        wheel.write_bytes(self.content)
        value = ci_environment.wheel_requirement(
            "aixand-undercurrent-sdk==0.1.0b0\n",
            "aixand-undercurrent-sdk",
            "0.1.0b0",
            wheel,
        )
        self.assertIn("%20", value)
        self.assertIn("#sha256=" + published.sha(self.content), value)
        with self.assertRaisesRegex(ValueError, "Regenerate"):
            ci_environment.wheel_requirement(
                "aixand-undercurrent-sdk==0.2.0\n",
                "aixand-undercurrent-sdk",
                "0.1.0b0",
                wheel,
            )


class IntegrationPolicyTests(unittest.TestCase):
    def test_normal_checks_do_not_clone_published_dependency_repositories(self):
        for product in (
            "agent-manager",
            "conversation-manager",
            "mcp-manager",
            "skill-manager",
        ):
            with self.subTest(product=product):
                sources = ci_release.source_outputs(product)
                for name in ("libraries", "undercurrent"):
                    self.assertEqual(sources[name + "-needed"], "false")

    def test_source_integration_is_explicit_and_cannot_publish(self):
        sources = ci_release.source_outputs("agent-manager", local=True)
        self.assertEqual(sources["libraries-needed"], "true")
        self.assertEqual(sources["undercurrent-needed"], "true")
        with self.assertRaisesRegex(ValueError, "published external"):
            ci_release.source_outputs("agent-manager", release=True, local=True)

    def test_installer_uses_registry_libraries_and_exact_released_sdk_hash(self):
        lock = tomllib.loads((ROOT / "installer/uv.lock").read_text())
        packages = {item["name"]: item for item in lock["package"]}
        for name in (
            "aixand-lib-filesystem",
            "aixand-lib-local-runtime",
            "aixand-lib-github-source",
        ):
            self.assertEqual(
                packages[name]["source"], {"registry": "https://pypi.org/simple"}
            )
        self.assertEqual(
            packages["aixand-undercurrent-sdk"]["source"],
            {"registry": "https://pypi.org/simple"},
        )
        sdk = published.manifest()["aixand-undercurrent-sdk"]
        self.assertEqual(
            packages["aixand-undercurrent-sdk"]["wheels"][0]["hash"],
            "sha256:" + sdk["wheel"]["sha256"],
        )
        installer = tomllib.loads((ROOT / "installation.toml").read_text())
        self.assertEqual(set(installer.get("sources", {})), set())
        self.assertNotIn("aixand-undercurrent-sdk", installer["components"])


if __name__ == "__main__":
    unittest.main()
