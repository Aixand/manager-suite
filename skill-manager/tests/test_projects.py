import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_manager import FileContent, Manager, SkillError, fs


class CollectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = Manager(self.root / "store")
        self.collection = self.root / "collection"
        self.collection.mkdir()

    def package(self, relative, name):
        path = self.collection / relative
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Review work\n---\nInstructions\n"
        )
        return path

    def test_nested_selection_stops_at_packages_and_reimports_preserve_edits(self):
        first = self.package("first", "first")
        self.package("group/second", "second")
        self.package("first/support/nested", "nested")
        found = self.manager.discover_collection(self.collection)
        self.assertEqual(
            ["first", "group/second"], [row["path"] for row in found["candidates"]]
        )
        self.assertFalse(self.manager.home.exists())
        imported = self.manager.import_collection(
            self.collection, ["group/second"], expected_revision=found["revision"]
        )
        row = imported["items"][0]["status"]
        handle = self.manager.skill(row["publisher_id"], row["id"])
        edited = handle.save(
            {"notes.md": FileContent(b"custom")}, expected_revision=row["revision"]
        )
        repeated = self.manager.import_collection(self.collection, ["group/second"])[
            "items"
        ][0]["status"]
        self.assertEqual(edited["revision"], repeated["revision"])
        self.assertEqual(b"custom", handle.resolve().read_file("notes.md"))
        self.assertTrue(first.is_dir())

    def test_invalid_selection_preflight_prevents_partial_import(self):
        self.package("first", "first")
        bad = self.package("bad", "bad")
        (bad / "SKILL.md").write_bytes(b"Invalid metadata")
        with self.assertRaises(SkillError):
            self.manager.import_collection(self.collection, all=True)
        self.assertFalse(self.manager.home.exists())
        old = self.manager.discover_collection(self.collection)
        (bad / "SKILL.md").write_bytes(b"Still invalid")
        with self.assertRaises(SkillError):
            self.manager.import_collection(
                self.collection, ["first"], expected_revision=old["revision"]
            )
        with self.assertRaises(SkillError):
            self.manager.import_collection(self.collection, ["../escape"])

    def test_linked_collection_paths_are_not_followed(self):
        source = self.package("first", "first")
        try:
            (self.collection / "linked").symlink_to(source, target_is_directory=True)
        except OSError:
            self.skipTest("Symlinks unavailable")
        found = self.manager.discover_collection(self.collection)
        self.assertEqual(["first"], [row["path"] for row in found["candidates"]])
        self.assertTrue(found["diagnostics"])

    def test_collection_aggregate_limit_is_checked_before_any_import(self):
        self.package("first", "first")
        self.package("second", "second")
        with patch("skill_manager.collections.MAX_IMPORT_BYTES", 1):
            with self.assertRaises(SkillError):
                self.manager.import_collection(self.collection, all=True)
        self.assertFalse(self.manager.home.exists())

    def test_cli_discovery_and_explicit_collection_import(self):
        self.package("group/first", "first")
        command = [
            sys.executable,
            "-m",
            "skill_manager",
            "--home",
            str(self.manager.home),
            "import",
            str(self.collection),
        ]
        found = subprocess.run(command, text=True, capture_output=True, timeout=20)
        self.assertEqual(0, found.returncode, found.stderr)
        discovery = json.loads(found.stdout)
        self.assertEqual("group/first", discovery["candidates"][0]["path"])
        self.assertFalse(self.manager.home.exists())
        imported = subprocess.run(
            [*command, "--all", "--expected-revision", discovery["revision"]],
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(0, imported.returncode, imported.stderr)
        self.assertEqual(1, len(json.loads(imported.stdout)["items"]))

    def test_cli_discovery_rejects_unused_revision_guard(self):
        self.package("first", "first")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "skill_manager",
                "--home",
                str(self.manager.home),
                "import",
                str(self.collection),
                "--expected-revision",
                "stale",
            ],
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(2, result.returncode)
        error = json.loads(result.stderr)["error"]
        self.assertEqual("validation", error["code"])
        self.assertIn("--select or --all", error["message"])
        self.assertFalse(self.manager.home.exists())


class ProjectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = Manager(self.root / "store")
        self.manager.create_managed(
            "sample", content="Original", description="Review work"
        )
        self.skill = self.manager.skill("local", "sample")
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.project = self.manager.project(self.repo)

    def connect(self):
        return self.project.connect(
            self.project.preview_connect([self.skill.reference], skills_dir="skills")
        )

    def test_connection_selectors_are_consistent_across_sdk_operations(self):
        self.connect()
        for selector in (
            self.skill,
            self.skill.resolve(),
            self.skill.reference,
            self.skill.reference.to_dict(),
            "local/sample",
            "sample",
        ):
            with self.subTest(selector=type(selector).__name__):
                self.assertEqual([], self.project.preview_update([selector])["changes"])
                self.assertEqual(
                    "remove",
                    self.project.preview_disconnect([selector])["changes"][0]["kind"],
                )
        foreign = Manager(self.root / "other-store").skill("local", "sample")
        for selector in (foreign, foreign.reference.to_dict()):
            for operation in (
                self.project.preview_update,
                self.project.preview_disconnect,
            ):
                with self.assertRaises(SkillError) as failure:
                    operation([selector])
                self.assertEqual("conflict", failure.exception.code)

    def test_disconnect_requires_explicit_nonempty_selection(self):
        self.connect()
        for selection in (None, [], "sample", [None], [{}]):
            with self.subTest(selection=selection):
                with self.assertRaises(SkillError) as failure:
                    self.project.disconnect(selection)
                self.assertEqual("validation", failure.exception.code)
                self.assertTrue((self.repo / "skills/sample/SKILL.md").exists())
        self.assertEqual(1, len(self.project.connections()["connections"]))

    def update_library(self):
        old = self.skill.resolve()
        return self.skill.save(
            {
                "notes.md": FileContent(b"Updated\n"),
                "image.bin": FileContent(b"\x00\xff"),
            },
            expected_revision=old.revision,
        )

    def fail_publication(self, source, destination):
        if "staged" in source.parts:
            raise OSError("simulated publication interruption")
        return self.rename(source, destination)

    def interrupt_update(self):
        self.rename = fs.rename_absent
        with patch(
            "skill_manager.projects.fs.rename_absent", side_effect=self.fail_publication
        ):
            with self.assertRaises(SkillError) as failure:
                self.project.apply_update(self.project.preview_update())
        self.assertTrue(failure.exception.uncertain)
        return failure.exception.operation_id

    def test_preview_pinning_two_projects_and_safe_update(self):
        plan = self.project.preview_connect([self.skill.reference], skills_dir="skills")
        self.assertFalse((self.repo / ".skill-manager").exists())
        old = self.skill.resolve()
        self.update_library()
        receipt = self.project.connect(json.loads(json.dumps(plan)))
        self.assertEqual("completed", receipt["status"])
        self.assertFalse((self.repo / "skills/sample/notes.md").exists())
        self.assertTrue(
            self.project.connections()["connections"][0]["update_available"]
        )
        other = self.root / "other"
        other.mkdir()
        second = self.manager.project(other)
        second.connect(second.preview_connect(["sample"], skills_dir="custom/skills"))
        update = self.project.preview_update()
        self.project.apply_update(update)
        self.assertEqual(
            b"Updated\n", (self.repo / "skills/sample/notes.md").read_bytes()
        )
        self.assertEqual(
            b"\x00\xff", (other / "custom/skills/sample/image.bin").read_bytes()
        )
        self.assertNotEqual(
            old.digest, self.project.connections()["connections"][0]["digest"]
        )
        self.assertEqual(receipt, self.project.connect(plan))
        manifest = json.loads((self.repo / ".skill-manager/project.json").read_text())
        self.assertNotIn(str(self.manager.home), json.dumps(manifest))

    def test_stale_plans_and_tampered_plans_preserve_destinations(self):
        plan = self.project.preview_connect(["sample"], skills_dir="skills")
        forged = json.loads(json.dumps(plan))
        forged["after"]["skills_dir"] = "../outside"
        with self.assertRaises(SkillError):
            self.project.apply(forged)
        (self.repo / "skills/sample").mkdir(parents=True)
        (self.repo / "skills/sample/unrelated").write_bytes(b"keep")
        with self.assertRaises(SkillError):
            self.project.connect(plan)
        self.assertEqual(b"keep", (self.repo / "skills/sample/unrelated").read_bytes())

    def test_explicit_identical_adoption_and_name_collision(self):
        (self.repo / "skills").mkdir()
        self.skill.resolve().export(self.repo / "skills/sample")
        with self.assertRaises(SkillError):
            self.project.preview_connect(["sample"], skills_dir="skills")
        plan = self.project.preview_connect(["sample"], skills_dir="skills", adopt=True)
        self.project.connect(plan)
        source = self.root / "sample"
        shutil.copytree(self.repo / "skills/sample", source)
        self.manager.ensure_registered(
            "other", source, skill_id="sample", publisher_revision=1
        )
        with self.assertRaises(SkillError):
            self.project.preview_connect(["other/sample"])

    def test_project_edits_variant_forget_and_reconnect_preserve_shared_library(self):
        self.connect()
        path = self.repo / "skills/sample/SKILL.md"
        path.write_bytes(path.read_bytes() + b"\nProject instructions\n")
        report = self.project.connections()
        row = report["connections"][0]
        self.assertEqual("modified", row["status"])
        for method in (
            self.project.preview_update,
            lambda: self.project.preview_disconnect(["sample"]),
        ):
            with self.assertRaises(SkillError):
                method()
        copied = self.project.save_variant(
            "sample",
            "project-sample",
            expected_revision=report["revision"],
            expected_digest=row["observed"]["digest"],
        )
        self.assertFalse(copied["tracking"])
        self.assertNotIn(
            b"Project instructions", self.skill.resolve().read_file("SKILL.md")
        )
        self.project.disconnect(["sample"], forget=True)
        self.assertTrue(path.exists())
        self.project.connect(
            self.project.preview_connect(["local/project-sample"], adopt=True)
        )
        self.assertEqual(
            "project-sample", self.project.connections()["connections"][0]["skill_id"]
        )

    def test_disconnect_moves_only_unchanged_owned_package(self):
        self.connect()
        (self.repo / "skills/unrelated").mkdir()
        (self.repo / "skills/unrelated/file").write_bytes(b"keep")
        receipt = self.project.disconnect(["sample"])
        self.assertFalse((self.repo / "skills/sample").exists())
        self.assertEqual(b"keep", (self.repo / "skills/unrelated/file").read_bytes())
        self.assertTrue(
            (
                self.repo
                / ".skill-manager/operations"
                / receipt["id"]
                / "previous/sample/SKILL.md"
            ).is_file()
        )
        self.assertEqual([], self.project.connections()["connections"])

    def test_interrupted_update_can_resume_after_restart(self):
        self.connect()
        self.update_library()
        identifier = self.interrupt_update()
        self.assertFalse((self.repo / "skills/sample").exists())
        with self.assertRaises(SkillError):
            self.project.preview_update()
        self.assertEqual(identifier, self.project.connections()["pending_operation"])
        restarted = Manager(self.manager.home).project(self.repo)
        receipt = restarted.recover(identifier)
        self.assertEqual("completed", receipt["status"])
        self.assertEqual(
            b"Updated\n", (self.repo / "skills/sample/notes.md").read_bytes()
        )
        self.assertIsNone(restarted.connections()["pending_operation"])

    def test_interrupted_update_can_roll_back_without_losing_original(self):
        self.connect()
        original = (self.repo / "skills/sample/SKILL.md").read_bytes()
        self.update_library()
        identifier = self.interrupt_update()
        self.assertEqual(
            "rolled_back", self.project.recover(identifier, action="rollback")["status"]
        )
        self.assertEqual(original, (self.repo / "skills/sample/SKILL.md").read_bytes())
        self.assertFalse((self.repo / "skills/sample/notes.md").exists())
        self.project.apply_update(self.project.preview_update())

    def test_partial_metadata_commit_can_be_completed(self):
        self.connect()
        self.update_library()
        write = self.manager._store.put_json

        def fail_state(path, value, **kwargs):
            if path.name == "state.json" and "projects" in path.parts:
                raise OSError("state pointer failure")
            return write(path, value, **kwargs)

        with patch.object(self.manager._store, "put_json", side_effect=fail_state):
            with self.assertRaises(SkillError) as failure:
                self.project.apply_update(self.project.preview_update())
        identifier = failure.exception.operation_id
        with self.assertRaises(SkillError):
            self.project.recover(identifier, action="rollback")
        self.project.recover(identifier)
        self.assertEqual(
            "installed", self.project.connections()["connections"][0]["status"]
        )

    def test_competing_identical_directory_is_not_claimed_or_removed(self):
        plan = self.project.preview_connect(["sample"], skills_dir="skills")
        rename = fs.rename_absent

        def competing(source, target):
            if "staged" in source.parts:
                shutil.copytree(source, target)
            return rename(source, target)

        with patch("skill_manager.projects.fs.rename_absent", side_effect=competing):
            with self.assertRaises(SkillError) as failure:
                self.project.connect(plan)
        identifier = failure.exception.operation_id
        self.assertTrue((self.repo / "skills/sample/SKILL.md").is_file())
        with self.assertRaises(SkillError):
            self.project.recover(identifier, action="rollback")
        self.assertTrue((self.repo / "skills/sample/SKILL.md").is_file())

    def test_edits_racing_backup_are_preserved_by_rollback(self):
        self.connect()
        self.update_library()
        rename = fs.rename_absent

        def edit_backup(source, target):
            rename(source, target)
            if "previous" in target.parts:
                path = target / "SKILL.md"
                path.write_bytes(path.read_bytes() + b"Racing edit")

        with patch("skill_manager.projects.fs.rename_absent", side_effect=edit_backup):
            with self.assertRaises(SkillError) as failure:
                self.project.apply_update(self.project.preview_update())
        self.project.recover(failure.exception.operation_id, action="rollback")
        self.assertIn(
            b"Racing edit", (self.repo / "skills/sample/SKILL.md").read_bytes()
        )

    def test_restore_portable_manifest_into_another_project_and_store(self):
        self.connect()
        other = self.root / "clone"
        (other / ".skill-manager").mkdir(parents=True)
        shutil.copy(
            self.repo / ".skill-manager/project.json",
            other / ".skill-manager/project.json",
        )
        clone = self.manager.project(other)
        clone.connect(clone.preview_restore())
        self.assertTrue((other / "skills/sample/SKILL.md").is_file())
        replacement = Manager(self.root / "replacement-store")
        replacement.create_managed(
            "sample", content="Original", description="Review work"
        )
        new_project = replacement.project(other)
        with self.assertRaises(SkillError):
            new_project.preview_restore()
        new_project.connect(new_project.preview_restore(adopt=True))
        with self.assertRaises(SkillError):
            clone.preview_update()

    def test_restore_missing_files_and_manifest_uses_pinned_content(self):
        self.connect()
        self.update_library()
        shutil.rmtree(self.repo / "skills/sample")
        (self.repo / ".skill-manager/project.json").unlink()
        self.project.connect(self.project.preview_restore())
        self.assertTrue((self.repo / "skills/sample/SKILL.md").is_file())
        self.assertFalse((self.repo / "skills/sample/notes.md").exists())

    def test_symlink_destination_cannot_escape_project(self):
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (self.repo / "skills").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlinks unavailable")
        plan = self.project.preview_connect(["sample"], skills_dir="skills")
        with self.assertRaises(SkillError):
            self.project.connect(plan)
        self.assertEqual([], list(outside.iterdir()))

    def test_guarded_paths_and_project_identity_are_isolated(self):
        for directory in (
            "../outside",
            ".skill-manager/skills",
            ".git/skills",
            "/absolute",
        ):
            with self.subTest(directory=directory), self.assertRaises(SkillError):
                self.project.preview_connect(["sample"], skills_dir=directory)
        plan = self.project.preview_connect(["sample"], skills_dir="skills")
        other = self.root / "same-name" / "repo"
        other.mkdir(parents=True)
        with self.assertRaises(SkillError):
            self.manager.project(other).apply(plan)

    def test_incomplete_stage_is_retained_and_rebuilt_on_explicit_recovery(self):
        plan = self.project.preview_connect(["sample"], skills_dir="skills")

        def incomplete(path, directories, files):
            (path / "partial").write_bytes(b"retained")
            raise OSError("stage interruption")

        with patch.object(self.manager, "_materialize", side_effect=incomplete):
            with self.assertRaises(SkillError) as failure:
                self.project.connect(plan)
        self.assertFalse((self.repo / "skills/sample").exists())
        identifier = failure.exception.operation_id
        self.project.recover(identifier)
        self.assertTrue((self.repo / "skills/sample/SKILL.md").is_file())
        self.assertTrue(
            list(
                (self.repo / ".skill-manager/operations" / identifier).glob(
                    "incomplete-*/sample/partial"
                )
            )
        )

    def test_manifest_changes_invalidate_plans_and_restore_is_explicit(self):
        self.connect()
        plan = self.project.preview_update()
        path = self.repo / ".skill-manager/project.json"
        manifest = json.loads(path.read_text())
        manifest["skills"] = []
        path.write_text(json.dumps(manifest))
        with self.assertRaises(SkillError):
            self.project.apply_update(plan)
        self.project.connect(self.project.preview_restore())
        self.assertFalse((self.repo / "skills/sample").exists())

    def test_cli_collection_connect_status_update_and_disconnect(self):
        def cli(*args):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "skill_manager",
                    "--home",
                    str(self.manager.home),
                    *args,
                ],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            return json.loads(result.stdout)

        plan = cli(
            "connect",
            "sample",
            "--project",
            str(self.repo),
            "--skills-dir",
            "skills",
            "--preview",
        )
        self.assertFalse((self.repo / "skills").exists())
        receipt = cli(
            "apply-project-plan",
            "--project",
            str(self.repo),
            "--plan",
            json.dumps(plan),
        )
        self.assertEqual("completed", receipt["status"])
        self.assertEqual(
            "installed",
            cli("connections", "--project", str(self.repo))["connections"][0]["status"],
        )
        self.update_library()
        cli("update-connections", "--project", str(self.repo))
        cli("disconnect", "sample", "--project", str(self.repo))
        self.assertFalse((self.repo / "skills/sample").exists())

    def test_two_processes_retrying_one_plan_share_one_operation(self):
        plan = self.project.preview_connect(["sample"], skills_dir="skills")
        command = [
            sys.executable,
            "-m",
            "skill_manager",
            "--home",
            str(self.manager.home),
            "apply-project-plan",
            "--project",
            str(self.repo),
            "--plan",
            json.dumps(plan),
        ]
        children = [
            subprocess.Popen(
                command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            for _ in range(2)
        ]
        try:
            outputs = []
            for child in children:
                stdout, stderr = child.communicate(timeout=20)
                self.assertEqual(0, child.returncode, stderr)
                outputs.append(json.loads(stdout))
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(1, len(self.project.operations()))
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=10)

    def test_partial_multi_package_install_can_be_rolled_back(self):
        self.manager.create_managed(
            "second", content="Second", description="Review work"
        )
        plan = self.project.preview_connect(["sample", "second"], skills_dir="skills")
        rename = fs.rename_absent

        def fail_second(source, target):
            if "staged" in source.parts and target.name == "second":
                raise OSError("second package interruption")
            return rename(source, target)

        with patch("skill_manager.projects.fs.rename_absent", side_effect=fail_second):
            with self.assertRaises(SkillError) as failure:
                self.project.connect(plan)
        self.assertTrue((self.repo / "skills/sample").is_dir())
        self.assertFalse((self.repo / "skills/second").exists())
        self.project.recover(failure.exception.operation_id, action="rollback")
        self.assertFalse((self.repo / "skills/sample").exists())
        self.assertFalse((self.repo / ".skill-manager/project.json").exists())
