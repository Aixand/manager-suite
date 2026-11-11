import ast
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_manager import (
    SDK_CONTRACT_VERSION,
    FileContent,
    Manager,
    RegistrationResult,
    ResolvedSkill,
    Skill,
    SkillError,
    SkillReference,
)


class SDKTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "sample"
        self.source.mkdir()
        (self.source / "SKILL.md").write_bytes(
            b"---\nname: sample\ndescription: Review changes.\n---\nInstructions.\n"
        )
        (self.source / "body.md").write_bytes(b"first\nmiddle\nlast\n")
        (self.source / "notes.md").write_bytes(b"notes\n")
        (self.source / ".hidden").write_bytes(b"hidden\r\n")
        (self.source / "image.bin").write_bytes(b"\x00\xff")
        (self.source / "script.py").write_bytes(b"print('original')\n")
        (self.source / "empty").mkdir()
        self.manager = Manager(self.root / "store")
        self.skill = self.manager.skill("publisher", "review")

    def register(self, revision=1):
        return self.skill.register(
            self.source, publisher_revision=revision, version=f"v{revision}"
        )

    def test_public_handle_identity_has_no_startup_side_effects(self):
        self.assertEqual(1, SDK_CONTRACT_VERSION)
        self.assertIsInstance(self.skill, Skill)
        self.assertFalse(self.manager.home.exists())
        serialized = json.loads(json.dumps(self.skill.reference.to_dict()))
        reference = SkillReference.from_dict(serialized)
        self.assertEqual(reference, self.manager.open_skill(serialized).reference)
        with self.assertRaises(SkillError) as error:
            Manager(self.root / "other").open_skill(reference)
        self.assertEqual("conflict", error.exception.code)
        for value in (
            {**serialized, "extra": True},
            {**serialized, "skill_id": "../escape"},
        ):
            with self.assertRaises(SkillError):
                self.manager.open_skill(value)
        self.assertFalse(self.manager.home.exists())

    def test_registration_ordering_effective_content_and_strict_refresh(self):
        first = self.register()
        self.assertIsInstance(first, RegistrationResult)
        self.assertIsInstance(first.content, ResolvedSkill)
        self.assertEqual("sample", first.content.name)
        self.assertEqual(first.status["revision"], first.content.revision)
        self.assertEqual(first.content.revision, self.register().content.revision)
        (self.source / "body.md").write_bytes(b"Publisher v2\n")
        updated = self.register(2)
        self.assertEqual(b"Publisher v2\n", updated.content.read_file("body.md"))
        self.assertEqual(b"first\nmiddle\nlast\n", first.content.read_file("body.md"))
        self.assertEqual("ignored_older", self.register(1).status["outcome"])
        (self.source / "body.md").write_bytes(b"Reused revision content\n")
        rejected = self.register(2)
        self.assertEqual("refresh_failed", rejected.status["outcome"])
        self.assertEqual("conflict", rejected.status["sync_error"]["code"])
        self.assertEqual(b"Publisher v2\n", rejected.content.read_file("body.md"))
        with self.assertRaises(SkillError):
            self.skill.refresh(self.source, publisher_revision=2)

    def test_customized_whole_package_stays_active_until_review(self):
        first = self.register()
        user_instructions = first.content.read_file("SKILL.md").replace(
            b"Instructions.", b"User instructions."
        )
        customized = self.skill.save(
            {
                "SKILL.md": FileContent(user_instructions),
                "body.md": FileContent(b"ours\nmiddle\nlast\n"),
                "image.bin": FileContent(b"\x00\x01"),
                "script.py": FileContent(b"print('user')\n", executable=True),
            },
            expected_revision=first.content.revision,
        )
        (self.source / "body.md").write_bytes(b"first\nmiddle\ntheirs\n")
        (self.source / "image.bin").write_bytes(b"\x00\x02")
        pending = self.register(2)
        self.assertTrue(pending.status["customized"])
        self.assertTrue(pending.status["reconciliation_required"])
        self.assertEqual(customized["active_digest"], pending.content.digest)
        self.assertEqual(user_instructions, pending.content.read_file("SKILL.md"))
        self.assertEqual(b"\x00\x01", pending.content.read_file("image.bin"))
        review = self.skill.preview_reconciliation()
        self.assertEqual(
            ["/files/image.bin/content_b64"],
            [item["path"] for item in review["conflicts"]],
        )
        with self.assertRaises(SkillError):
            self.skill.reconcile(expected_revision=review["revision"])
        accepted = self.skill.reconcile(
            expected_revision=review["revision"],
            resolutions={"/files/image.bin/content_b64": {"take": "publisher"}},
        )
        self.assertFalse(accepted["reconciliation_required"])
        current = self.skill.resolve()
        self.assertEqual(b"ours\nmiddle\ntheirs\n", current.read_file("body.md"))
        self.assertEqual(b"\x00\x02", current.read_file("image.bin"))
        self.assertEqual(user_instructions, current.read_file("SKILL.md"))
        self.assertTrue(
            next(row for row in current.files if row["path"] == "script.py")[
                "executable"
            ]
        )
        self.assertEqual(b"hidden\r\n", current.read_file(".hidden"))
        self.assertIn("empty", current.directories)
        self.assertEqual(accepted["revision"], self.register(2).content.revision)

    def test_validation_stale_edits_and_deleted_edited_file(self):
        first = self.register()
        candidate = self.skill.validate({"new.bin": FileContent(b"\xff\x00", True)})
        self.assertTrue(candidate["valid"])
        self.assertNotEqual(first.content.digest, candidate["digest"])
        self.assertEqual(first.content.revision, self.skill.status()["revision"])
        self.assertEqual(1, len(self.skill.history()))
        with self.assertRaises(SkillError):
            self.skill.validate({"SKILL.md": None})
        self.skill.save(
            {"notes.md": FileContent(b"My notes\n")},
            expected_revision=first.content.revision,
        )
        with self.assertRaises(SkillError):
            self.skill.save({}, expected_revision=first.content.revision)
        (self.source / "notes.md").unlink()
        self.register(2)
        review = self.skill.preview_reconciliation()
        self.assertEqual("/files/notes.md", review["conflicts"][0]["path"])
        self.skill.save(
            {"body.md": FileContent(b"Newer edit\n")},
            expected_revision=review["revision"],
        )
        with self.assertRaises(SkillError):
            self.skill.reconcile(expected_revision=review["revision"], take="publisher")
        fresh = self.skill.preview_reconciliation()
        self.skill.reconcile(expected_revision=fresh["revision"], take="managed")
        self.assertEqual(b"My notes\n", self.skill.resolve().read_file("notes.md"))

    def test_resolution_exports_and_publications_remain_pinned(self):
        original = self.register().content
        self.skill.save(
            {"body.md": FileContent(b"Current user edit\n")},
            expected_revision=original.revision,
        )
        self.assertNotEqual(original, self.skill.resolve())
        destination = self.root / "consumer" / original.name
        destination.parent.mkdir()
        receipt = original.export(destination)
        self.assertEqual(original.digest, receipt["digest"])
        self.assertEqual(
            b"first\nmiddle\nlast\n", (destination / "body.md").read_bytes()
        )
        self.assertEqual(b"\x00\xff", (destination / "image.bin").read_bytes())
        self.assertTrue((destination / "empty").is_dir())
        release = original.release("v1")
        self.assertEqual(original.digest, release["digest"])
        with self.assertRaises(SkillError):
            self.skill.release("v1")
        self.assertEqual(
            original.digest, self.skill.resolve(revision=original.revision).digest
        )
        self.assertEqual("identical", original.export(destination)["status"])
        (destination / "body.md").write_bytes(b"Destination edit")
        with self.assertRaises(SkillError):
            original.export(destination)
        self.assertEqual(b"Destination edit", (destination / "body.md").read_bytes())

    def test_failed_refresh_keeps_verified_copy_and_first_import_errors(self):
        original = self.register().content
        shutil.rmtree(self.source)
        with patch.object(
            self.manager,
            "check_updates",
            side_effect=AssertionError("Offline resolution fetched"),
        ):
            self.assertEqual(original.digest, self.skill.resolve().digest)
        fallback = self.register(2)
        self.assertEqual("refresh_failed", fallback.status["outcome"])
        self.assertEqual(original.digest, fallback.content.digest)
        self.assertIsNotNone(fallback.status["sync_error"])
        with self.assertRaises(SkillError):
            self.manager.skill("publisher", "absent").register(
                self.source, publisher_revision=1
            )
        with self.assertRaises(SkillError):
            self.skill.refresh(self.source, publisher_revision=2)
        (self.manager.home / "objects" / (original.digest[7:] + ".json")).unlink()
        with self.assertRaises(SkillError):
            self.register(2)

    def test_registration_content_and_status_cannot_mix_with_concurrent_edit(self):
        from skill_manager.managed import resolved_snapshot

        def concurrent_edit(owner, status, snapshot):
            captured = resolved_snapshot(owner, status, snapshot)
            owner.save_managed(
                "publisher",
                "review",
                {"body.md": {"content": b"Later edit", "executable": False}},
                expected_revision=status["revision"],
            )
            return captured

        with patch(
            "skill_manager.managed.resolved_snapshot", side_effect=concurrent_edit
        ):
            captured = self.register()
        self.assertEqual(captured.status["revision"], captured.content.revision)
        self.assertNotEqual(captured.content.revision, self.skill.status()["revision"])
        self.assertEqual(
            b"first\nmiddle\nlast\n", captured.content.read_file("body.md")
        )
        self.assertEqual(b"Later edit", self.skill.resolve().read_file("body.md"))

    def test_history_compare_restore_tracking_and_bounded_inspection(self):
        original = self.register().content
        saved = self.skill.save(
            {"body.md": FileContent(b"Changed\n")}, expected_revision=original.revision
        )
        compared = self.skill.compare(original.revision)
        self.assertEqual(original.revision, compared["before"]["revision"])
        self.assertEqual(saved["revision"], compared["after"]["revision"])
        self.assertTrue(compared["changes"])
        self.assertEqual(
            [], self.skill.compare(original.revision, original.revision)["changes"]
        )
        binary = self.skill.inspect(path="/files/image.bin")["sides"]["managed"]
        self.assertTrue(binary["binary"])
        self.assertNotIn("content_b64", binary)
        restored = self.skill.rollback(
            original.revision, expected_revision=saved["revision"]
        )
        self.assertEqual(original.digest, restored["active_digest"])
        paused = self.skill.set_tracking(False, expected_revision=restored["revision"])
        self.assertFalse(paused["tracking"])
        self.assertEqual("tracking_stopped", self.register(2).status["outcome"])

    def test_result_serialization_and_defensive_metadata(self):
        result = self.register()
        serialized = json.loads(json.dumps(result.to_dict()))
        self.assertEqual("skill-manager/registration-result-1", serialized["schema"])
        result.status["revision"] = "modified"
        result.content.manifest["files"].clear()
        result.content.to_dict()["resolution"]["revision"] = "modified"
        self.assertEqual(serialized["status"]["revision"], result.content.revision)
        self.assertTrue(result.content.files)
        reopened = Manager(self.manager.home).open_skill(self.skill.reference.to_dict())
        self.assertEqual(result.content.digest, reopened.resolve().digest)
        self.assertEqual(
            {"content_b64": "AP8=", "executable": True},
            FileContent(b"\x00\xff", True).to_dict(),
        )
        with self.assertRaises(SkillError):
            FileContent("text")

    def test_sdk_filesystem_failures_are_structured_and_interface_uses_public_manager_only(
        self,
    ):
        original = self.register().content
        with patch.object(
            self.manager._store, "capture", side_effect=OSError("disk full")
        ):
            with self.assertRaises(SkillError) as error:
                self.skill.save(
                    {"body.md": FileContent(b"Candidate")},
                    expected_revision=original.revision,
                )
        self.assertEqual("io", error.exception.code)
        import skill_manager.sdk

        tree = ast.parse(Path(skill_manager.sdk.__file__).read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "_manager"
            ):
                self.assertFalse(node.attr.startswith("_"), node.attr)
        package = Path(skill_manager.__file__).parent
        self.assertTrue((package / "py.typed").is_file())

    def test_source_handle_routes_through_existing_acquisition(self):
        from test_sources import GitHubFixture

        self.manager.source_client = GitHubFixture(
            {"skills/sample/SKILL.md": (self.source / "SKILL.md").read_bytes()}
        )
        discovery = self.manager.discover_source("https://github.com/example/resources")
        imported = self.manager.import_source(discovery["discovery_id"])["items"][0]
        handle = self.manager.skill(imported["publisher_id"], imported["id"])
        self.assertIsNotNone(handle.source_status()["source"])
        pinned = handle.source_settings(
            expected_revision=imported["revision"], pinned=True
        )
        self.assertEqual("pinned", handle.check_updates(force=True)["outcome"])
        switched = handle.switch_source(
            expected_revision=pinned["revision"], ref="other", track=True
        )
        self.assertTrue(switched["reconciliation_required"])
        handle.reconcile(expected_revision=switched["revision"], take="managed")

    def test_runnable_application_example(self):
        example = Path(__file__).resolve().parents[1] / "examples/sdk_application.py"
        result = subprocess.run(
            [sys.executable, "-B", str(example)],
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["pending_review_seen"])
        self.assertTrue(report["customized"])
        self.assertEqual("exported", report["exported"])
