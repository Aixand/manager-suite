import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from skill_manager import Manager, SkillError
from skill_manager import fs
from skill_manager.codec import canonical, digest, loads
from skill_manager.store import Store


def skill_files(name="csv-audit", description="Audit CSV structure and missing values when reviewing datasets."):
    return {
        "SKILL.md": {"content": (f"---\nname: {name}\ndescription: {description}\nlicense: MIT\n"
                                  "compatibility: Requires Python 3.11+\nmetadata:\n  author: aixand\n"
                                  "allowed-tools: Read\n---\nReview references/rules.md before using scripts/audit.py.\n").encode(), "executable": False},
        "scripts/audit.py": {"content": b"#!/usr/bin/env python3\nraise RuntimeError('MUST NEVER EXECUTE')\n", "executable": True},
        "references/rules.md": {"content": b"# Rules\nReport missing values; do not rewrite the input.\n", "executable": False},
        "assets/sample.bin": {"content": b"\x00\xff\x80\r\n\x00\x01", "executable": False},
        ".settings": {"content": b"hidden: included\n", "executable": False},
    }


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skill-manager-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "store"
        self.source = self.root / "authoring"
        self.source.mkdir()
        self.manager = Manager(self.home)
        self.registration = self.manager.register_source("local", [self.source], writable=True)
        self.entry = self.manager.create_draft("local", "csv-audit", skill_files())

    def release(self, version="v1", entry=None):
        current = entry or self.manager.show(self.entry["id"])
        return self.manager.release(current["id"], version, expected_digest=current["digest"])

    def assertError(self, code, function, *args, **kwargs):
        with self.assertRaises(SkillError) as result:
            function(*args, **kwargs)
        self.assertEqual(code, result.exception.code, result.exception)
        return result.exception


class ManagerTests(Fixture):
    def test_read_only_absent_store_and_home_precedence(self):
        absent = self.root / "absent"
        manager = Manager(absent)
        self.assertEqual([], manager.catalog()["entries"])
        self.assertEqual([], manager.sources())
        self.assertFalse(absent.exists())
        with patch.dict(os.environ, {"SKILL_MANAGER_HOME": str(absent), "XDG_DATA_HOME": str(self.root / "xdg")}):
            self.assertEqual(absent, Manager().home)
            self.assertEqual(self.home, Manager(self.home).home)
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "xdg")}, clear=True):
            self.assertEqual(self.root / "xdg/aixand/manager/skill", Manager().home)
        self.assertEqual(digest({"kind": "skill", "home": str(self.home)}), self.manager.store_id)

    def test_registry_guards_and_owner_revision(self):
        self.assertError("conflict", self.manager.register_source, "local", [self.source])
        previous = self.registration["revision"]
        row = self.manager.register_source("local", [self.source], owner={"plugin": "fixture"}, writable=False,
                                           expected_revision=previous)
        self.assertNotEqual(previous, row["revision"])
        self.assertError("conflict", self.manager.unregister_source, "local", expected_revision=previous)
        self.assertError("unsupported", self.manager.save_file, self.entry["id"], "assets/new.bin", b"data",
                         expected_digest=self.entry["digest"])
        self.assertError("unsupported", self.manager.create_draft, "local", "other", skill_files("other"))
        self.assertTrue(self.manager.validate(self.entry["id"])["valid"])
        self.manager.unregister_source("local", expected_revision=row["revision"])
        self.assertEqual([], self.manager.catalog()["entries"])
        self.assertTrue((self.source / "csv-audit").exists())

    def test_source_scoped_collision_and_same_source_namespace(self):
        other = self.root / "plugin"
        other.mkdir()
        self.manager.register_source("plugin", [other], writable=True)
        second = self.manager.create_draft("plugin", "csv-audit", skill_files())
        self.assertNotEqual(second["id"], self.entry["id"])
        self.assertEqual(second["digest"], self.entry["digest"])
        self.assertEqual(2, len(self.manager.catalog()["entries"]))
        self.assertError("validation", self.manager.show, "csv-audit")
        a, b = self.release(), self.release(entry=second)
        self.assertEqual("local/csv-audit@v1", a["reference"])
        self.assertEqual("plugin/csv-audit@v1", b["reference"])
        self.assertEqual(second["digest"], self.manager.resolve(b["reference"])["digest"])
        self.assertEqual(digest([self.manager.store_id, "plugin", second["path"]]), second["id"])

    def test_readonly_source_inspection_never_mutates_plugin_cache(self):
        package = self.source / "csv-audit"
        before = {str(p.relative_to(package)): (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
                  for p in package.rglob("*") if p.is_file()}
        self.manager.register_source("installed", [package], owner={"kind": "plugin"})
        entry = next(e for e in self.manager.catalog()["entries"] if e["source_id"] == "installed")
        self.assertFalse(entry["editable"])
        self.assertTrue(self.manager.validate(entry["id"])["valid"])
        self.assertEqual(skill_files()["assets/sample.bin"]["content"], self.manager.read_file(entry["id"], "assets/sample.bin"))
        self.manager.show(entry["id"])
        after = {str(p.relative_to(package)): (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
                 for p in package.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_bounded_discovery_direct_root_and_unavailable_registration(self):
        nested = self.source / "unrelated" / "deep" / "csv-audit"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_bytes(skill_files()["SKILL.md"]["content"])
        self.manager.register_source("direct", [self.source / "csv-audit"])
        missing = self.manager.register_source("missing", [self.root / "not-installed"])
        self.assertFalse(missing["availability"][0]["available"])
        entries = self.manager.catalog()["entries"]
        self.assertEqual({"local", "direct"}, {e["source_id"] for e in entries})
        self.assertError("validation", self.manager.create_draft, "local", "other", skill_files("other"), path=self.root)

    def test_multiple_roots_and_direct_root_creation(self):
        second = self.root / "second"
        second.mkdir()
        self.manager.register_source("many", [self.source, second], writable=True)
        self.assertError("validation", self.manager.create_draft, "many", "other", skill_files("other"))
        entry = self.manager.create_draft("many", "other", skill_files("other"), path=second)
        self.assertEqual(str(second / "other"), entry["path"])
        self.manager.register_source("direct", [self.source / "csv-audit"], writable=True)
        self.assertError("validation", self.manager.create_draft, "direct", "nested", skill_files("nested"))

    def test_binary_scripts_references_hidden_and_empty_directory_export(self):
        package = self.source / "csv-audit"
        (package / "assets" / "empty").mkdir()
        original = self.manager.show(self.entry["id"])
        released = self.release(entry=original)
        resolved = self.manager.resolve(released["reference"])
        self.assertIn("assets/empty", resolved["manifest"]["directories"])
        self.assertEqual(original["manifest"], resolved["manifest"])
        target = self.root / "csv-audit"
        receipt = self.manager.export(released["reference"], target)
        self.assertEqual("exported", receipt["status"])
        self.assertEqual(released["digest"], fs.capture(target)["digest"])
        for rel, spec in skill_files().items():
            self.assertEqual(spec["content"], (target / rel).read_bytes())
            self.assertEqual(0o755 if spec["executable"] else 0o644, stat.S_IMODE((target / rel).stat().st_mode))
        self.assertEqual("identical", self.manager.export(released["reference"], target)["status"])
        self.assertEqual(0o755, stat.S_IMODE((target / "assets" / "empty").stat().st_mode))

    def test_supporting_only_changes_and_permission_identity(self):
        first = self.release()
        current = self.manager.save_file(self.entry["id"], "assets/sample.bin", b"\x00\xffchanged",
                                         expected_digest=self.entry["digest"])
        second = self.release("v2", current)
        self.assertNotEqual(first["digest"], second["digest"])
        difference = self.manager.diff(first["reference"], second["reference"])
        self.assertEqual(["assets/sample.bin"], [r["path"] for r in difference["changed"]])
        self.assertFalse(difference["metadata_changed"])
        self.assertTrue(difference["changed"][0]["bytes_changed"])
        current = self.manager.save_file(current["id"], "scripts/audit.py", skill_files()["scripts/audit.py"]["content"],
                                         executable=False, expected_digest=current["digest"])
        third = self.release("v3", current)
        delta = self.manager.diff(second["reference"], third["reference"])
        self.assertTrue(delta["changed"][0]["executable_changed"])
        self.assertFalse(delta["changed"][0]["bytes_changed"])
        self.assertNotEqual(second["digest"], third["digest"])

    def test_diff_added_removed_directories_and_metadata(self):
        first = self.release()
        package = self.source / "csv-audit"
        (package / "references" / "rules.md").unlink()
        shutil.rmtree(package / "references")
        (package / "empty").mkdir()
        current = self.manager.show(self.entry["id"])
        current = self.manager.save_file(current["id"], "notes/new.md", b"added", expected_digest=current["digest"])
        current = self.manager.save_file(current["id"], "SKILL.md", skill_files(description="Different description.")["SKILL.md"]["content"],
                                         expected_digest=current["digest"])
        delta = self.manager.diff(first["reference"], current["id"])
        self.assertEqual(["notes/new.md"], [row["path"] for row in delta["added"]])
        self.assertEqual(["references/rules.md"], [row["path"] for row in delta["removed"]])
        self.assertEqual(["empty", "notes"], delta["directories_added"])
        self.assertEqual(["references"], delta["directories_removed"])
        self.assertTrue(delta["metadata_changed"])

    def test_stale_edit_and_release_are_rejected(self):
        current = self.manager.save_file(self.entry["id"], "references/rules.md", b"new rules", expected_digest=self.entry["digest"])
        self.assertError("conflict", self.manager.save_file, self.entry["id"], "references/rules.md", b"stale", expected_digest=self.entry["digest"])
        self.assertError("conflict", self.manager.release, self.entry["id"], "bad", expected_digest=self.entry["digest"])
        self.assertEqual(b"new rules", self.manager.read_file(current["id"], "references/rules.md"))

    def test_invalid_draft_repair_and_publish_validation(self):
        broken = self.manager.save_file(self.entry["id"], "SKILL.md", b"---\nname: WRONG\n---\n",
                                        expected_digest=self.entry["digest"])
        self.assertEqual("draft-files", broken["digest_kind"])
        self.assertFalse(self.manager.validate(broken["id"])["valid"])
        self.assertError("validation", self.manager.release, broken["id"], "invalid", expected_digest=broken["digest"])
        changed = self.manager.save_file(broken["id"], "references/rules.md", b"Repair notes", expected_digest=broken["digest"])
        self.assertNotEqual(broken["digest"], changed["digest"])
        fixed = self.manager.save_file(changed["id"], "SKILL.md", skill_files()["SKILL.md"]["content"], expected_digest=changed["digest"])
        self.assertEqual("package", fixed["digest_kind"])
        self.release(entry=fixed)

    def test_old_release_survives_draft_and_source_removal_and_reregistration(self):
        first = self.release()
        shutil.rmtree(self.source / "csv-audit")
        self.manager.unregister_source("local", expected_revision=self.registration["revision"])
        self.assertEqual([], self.manager.catalog()["entries"])
        self.assertEqual(first["digest"], self.manager.resolve(first["reference"])["digest"])
        self.assertEqual(first["digest"], self.manager.resolve("local/csv-audit@" + first["digest"])["digest"])
        other = self.root / "replacement-source"
        other.mkdir()
        self.manager.register_source("local", [other], owner="new owner")
        captured = self.manager.resolve(first["reference"])["release"]
        self.assertEqual(str(self.source / "csv-audit"), captured["source_path"])
        self.assertEqual(self.registration["revision"], captured["source_revision"])
        self.manager.export(first["reference"], self.root / "csv-audit")
        self.assertEqual(skill_files()["assets/sample.bin"]["content"], self.manager.read_file(first["reference"], "assets/sample.bin"))

    def test_immutable_label_conflict_and_same_content_idempotence(self):
        first = self.release()
        self.assertEqual(first, self.release())
        current = self.manager.save_file(self.entry["id"], "assets/sample.bin", b"new", expected_digest=self.entry["digest"])
        self.assertError("conflict", self.manager.release, current["id"], "v1", expected_digest=current["digest"])
        self.assertEqual(first, self.manager.resolve(first["reference"])["release"])

    def test_failed_publication_preserves_previous_release(self):
        first = self.release()
        current = self.manager.save_file(self.entry["id"], "assets/sample.bin", b"changed", expected_digest=self.entry["digest"])
        real_put = Store.put_json
        def fail_release(store, path, value, **kwargs):
            if "releases" in path.parts and path.name == "v2.json":
                raise OSError("injected release record failure")
            return real_put(store, path, value, **kwargs)
        with patch.object(Store, "put_json", fail_release):
            self.assertError("io", self.manager.release, current["id"], "v2", expected_digest=current["digest"])
        self.assertEqual(first["digest"], self.manager.resolve(first["reference"])["digest"])
        self.assertError("not_found", self.manager.resolve, "local/csv-audit@v2")
        self.assertError("not_found", self.manager.resolve, "local/csv-audit@" + current["digest"])

    def test_publication_detects_external_edit_before_commit(self):
        first = self.release()
        capture = Store.capture
        def edit(store, snapshot):
            capture(store, snapshot)
            (self.source / "csv-audit" / "references" / "rules.md").write_bytes(b"changed externally")
        with patch.object(Store, "capture", edit):
            self.assertError("conflict", self.manager.release, self.entry["id"], "v2", expected_digest=self.entry["digest"])
        self.assertError("not_found", self.manager.resolve, "local/csv-audit@v2")
        self.assertEqual(first["digest"], self.manager.resolve(first["reference"])["digest"])

    def test_export_conflict_failure_and_racing_empty_destination(self):
        first = self.release()
        target = self.root / "csv-audit"
        self.manager.export(first["reference"], target)
        current = self.manager.save_file(self.entry["id"], "assets/sample.bin", b"different", expected_digest=self.entry["digest"])
        second = self.release("v2", current)
        self.assertError("conflict", self.manager.export, second["reference"], target)
        self.assertEqual(first["digest"], fs.capture(target)["digest"])
        parent = self.root / "another"
        parent.mkdir()
        with patch.object(fs, "rename_absent", side_effect=OSError("injected commit failure")):
            self.assertError("io", self.manager.export, first["reference"], parent / "csv-audit")
        self.assertEqual([], list(parent.iterdir()))
        real_rename = fs.rename_absent
        def competitor(stage, destination):
            destination.mkdir()
            real_rename(stage, destination)
        with patch.object(fs, "rename_absent", competitor):
            self.assertError("conflict", self.manager.export, first["reference"], parent / "csv-audit")
        self.assertEqual([], list((parent / "csv-audit").iterdir()))
        self.assertEqual(first["digest"], fs.capture(target)["digest"])

    def test_export_name_and_parent_checked_before_writes(self):
        first = self.release()
        self.assertError("validation", self.manager.export, first["reference"], self.root / "wrong-name")
        self.assertFalse((self.root / "wrong-name").exists())
        self.assertError("validation", self.manager.export, first["reference"], self.root / "missing-parent" / "csv-audit")
        self.assertFalse((self.root / "missing-parent").exists())

    def test_release_blob_and_manifest_integrity(self):
        first = self.release()
        resolved = self.manager.resolve(first["reference"])
        blob = Path(resolved["blobs"]["assets/sample.bin"])
        original = blob.read_bytes()
        blob.write_bytes(b"corrupted")
        self.assertError("integrity", self.manager.resolve, first["reference"])
        self.assertError("integrity", self.manager.export, first["reference"], self.root / "csv-audit")
        self.assertFalse((self.root / "csv-audit").exists())
        blob.write_bytes(original)
        Path(resolved["object"]).write_text('{"schema":"wrong"}')
        self.assertError("integrity", self.manager.resolve, first["reference"])

    def test_symlinks_special_files_and_containment(self):
        link = self.source / "csv-audit" / "assets" / "link"
        link.symlink_to("sample.bin")
        shown = self.manager.show(self.entry["id"])
        self.assertEqual("invalid", shown["status"])
        self.assertIn("Symlink", shown["error"]["message"])
        self.assertFalse(self.manager.validate(self.entry["id"])["valid"])
        self.assertError("validation", self.manager.read_file, self.entry["id"], "assets/link")
        link.unlink()
        os.mkfifo(link)
        self.assertFalse(self.manager.validate(self.entry["id"])["valid"])
        link.unlink()
        for rel in ("../escape", "/absolute", "a/../b", "a\\b", "C:/drive", "./x", "a//b", "nul", "a.", "a\0b"):
            with self.subTest(rel=rel):
                self.assertError("validation", self.manager.save_file, self.entry["id"], rel, b"x", expected_digest=self.entry["digest"])
        root_link = self.root / "root-link"
        root_link.symlink_to(self.source, target_is_directory=True)
        self.assertError("validation", self.manager.register_source, "link", [root_link])

    def test_case_and_unicode_collisions(self):
        for extra in ({"skill.md": {"content": b"x", "executable": False}},
                      {"assets/Caf\u00e9": {"content": b"x", "executable": False}, "assets/Cafe\u0301": {"content": b"y", "executable": False}}):
            with self.subTest(extra=extra):
                files = skill_files("other") | extra
                self.assertError("validation", self.manager.create_draft, "local", "other", files)
        self.assertError("validation", self.manager.save_file, self.entry["id"], "skill.md", b"x", expected_digest=self.entry["digest"])
        self.assertEqual(self.entry["digest"], self.manager.show(self.entry["id"])["digest"])

    def test_metadata_failures_safe_yaml_and_actionable_errors(self):
        invalid = [b"not-frontmatter", b"---\nname: csv-audit\nname: csv-audit\ndescription: test\n---\n",
                   b"---\nname: csv-audit\ndescription: !!python/object/apply:os.system ['touch forbidden']\n---\n",
                   b"---\nname: csv-audit\ndescription: &value test\nlicense: *value\n---\n",
                   b"---\nname: csv-audit\ndescription: test\nmetadata:\n  version: 1\n---\n",
                   b"---\nname: csv-audit\ndescription: test\nunknown: true\n---\n",
                   b"---\nname: other\ndescription: test\n---\n", b"\xff",
                   ("---\nname: csv-audit\ndescription: " + "a" * 1025 + "\n---\n").encode()]
        for content in invalid:
            with self.subTest(content=content[:60]):
                current = self.manager.show(self.entry["id"])
                current = self.manager.save_file(current["id"], "SKILL.md", content, expected_digest=current["digest"])
                self.assertEqual("draft-files", current["digest_kind"])
                self.assertEqual("validation", current["error"]["code"])
                self.assertError("validation", self.manager.release, current["id"], "invalid", expected_digest=current["digest"])

    def test_corrupt_source_is_isolated(self):
        (self.home / "sources" / "broken.json").write_text('{"id":"x","id":"y"}')
        catalog = self.manager.catalog()
        self.assertEqual(1, len(catalog["entries"]))
        row = next(s for s in catalog["sources"] if s["id"] == "broken")
        self.assertEqual("integrity", row["error"]["code"])

    def test_validity_and_content_limits(self):
        with patch.object(fs, "MAX_FILE", 4):
            shown = self.manager.show(self.entry["id"])
            self.assertIsNone(shown["digest"])
            self.assertIn("exceeds", shown["error"]["message"])
        with patch.object(fs, "MAX_ENTRIES", 2):
            self.assertFalse(self.manager.validate(self.entry["id"])["valid"])

    def test_concurrent_revision_guard_only_one_writer_wins(self):
        code = '''
import sys
from skill_manager import Manager, SkillError
m = Manager(sys.argv[1])
try:
    m.save_file(sys.argv[2], "references/rules.md", sys.argv[4].encode(), expected_digest=sys.argv[3])
    print("saved")
except SkillError as e:
    print(e.code)
'''
        processes = [subprocess.Popen([sys.executable, "-c", code, str(self.home), self.entry["id"],
                                       self.entry["digest"], label], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for label in ("first", "second")]
        outcomes = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(0, process.returncode, stderr)
            outcomes.append(stdout.strip())
        self.assertEqual(["conflict", "saved"], sorted(outcomes))

    def test_fake_consumer_uses_only_public_release_and_export(self):
        first = self.release()
        consumer_home = self.root / "consumer-skills"
        consumer_home.mkdir()
        consumer = Manager(self.home)
        pinned = consumer.resolve(first["reference"])
        receipt = consumer.export(pinned["reference"], consumer_home / pinned["name"])
        snapshot = {"reference": pinned["reference"], "digest": pinned["digest"], "export": receipt}
        self.assertEqual(first["digest"], snapshot["export"]["digest"])
        self.assertTrue((Path(receipt["destination"]) / "scripts" / "audit.py").exists())

    def test_previous_version_listing_and_source_namespace_conflict(self):
        first = self.release()
        current = self.manager.save_file(self.entry["id"], "scripts/audit.py", b"#!/bin/sh\nexit 0\n", executable=True,
                                         expected_digest=self.entry["digest"])
        second = self.release("v2", current)
        self.assertNotEqual(first["digest"], second["digest"])
        self.assertEqual(["scripts/audit.py"], [row["path"] for row in self.manager.diff(first["reference"], second["reference"])["changed"]])
        another_root = self.root / "another-root"
        another_root.mkdir()
        self.manager.register_source("local", [self.source, another_root], writable=True,
                                     expected_revision=self.registration["revision"])
        duplicate = self.manager.create_draft("local", "csv-audit", skill_files(description="A competing same-named draft."), path=another_root)
        self.assertError("conflict", self.manager.release, duplicate["id"], "v1", expected_digest=duplicate["digest"])
        self.assertEqual([first, second], self.manager.releases("local", "csv-audit"))
        source = self.manager.source("local")
        self.manager.unregister_source("local", expected_revision=source["revision"])
        self.assertEqual([first, second], self.manager.releases())
        (self.home / "releases" / "local" / "csv-audit" / "broken.json").write_text("invalid")
        rows = self.manager.releases()
        self.assertEqual(3, len(rows))
        self.assertEqual("integrity", rows[0]["error"]["code"])

    def test_missing_and_symlinked_blob_are_integrity_errors(self):
        first = self.release()
        blob = Path(self.manager.resolve(first["reference"])["blobs"]["assets/sample.bin"])
        original = blob.read_bytes()
        blob.unlink()
        self.assertError("integrity", self.manager.resolve, first["reference"])
        self.assertFalse(self.manager.validate(first["reference"])["valid"])
        self.assertEqual("integrity", self.manager.validate(first["reference"])["diagnostics"][0]["code"])
        alternate = self.root / "outside-blob"
        alternate.write_bytes(original)
        blob.symlink_to(alternate)
        self.assertError("integrity", self.manager.read_file, first["reference"], "assets/sample.bin")
        self.assertError("integrity", self.manager.export, first["reference"], self.root / "csv-audit")
        self.assertEqual(original, alternate.read_bytes())

    def test_permission_capture_ignores_timestamps_and_other_mode_bits(self):
        script = self.source / "csv-audit" / "scripts" / "audit.py"
        script.chmod(0o711)
        os.utime(script, (10000, 10000))
        self.assertEqual(self.entry["digest"], self.manager.show(self.entry["id"])["digest"])
        script.chmod(0o644)
        self.assertNotEqual(self.entry["digest"], self.manager.show(self.entry["id"])["digest"])

    def test_external_directory_change_isolated_and_export_parent_symlink_rejected(self):
        first = self.release()
        (self.source / "csv-audit" / "SKILL.md").unlink()
        (self.source / "csv-audit" / "SKILL.md").symlink_to(self.root / "missing")
        self.assertFalse(self.manager.validate(self.entry["id"])["valid"])
        self.assertEqual(first["digest"], self.manager.resolve(first["reference"])["digest"])
        outside = self.root / "outside"
        outside.mkdir()
        parent = self.root / "symlink-parent"
        parent.symlink_to(outside, target_is_directory=True)
        self.assertError("io", self.manager.export, first["reference"], parent / "csv-audit")
        self.assertEqual([], list(outside.iterdir()))

    def test_forged_nonportable_manifest_is_rejected_even_with_matching_digest(self):
        first = self.release()
        resolved = self.manager.resolve(first["reference"])
        manifest = resolved["manifest"]
        manifest["files"][0]["path"] = "../escaped"
        identity = digest(manifest)
        (self.home / "objects" / (identity[7:] + ".json")).write_bytes(canonical(manifest))
        record = first.copy()
        record["digest"] = identity
        (self.home / "releases" / "local" / "csv-audit" / "v1.json").write_bytes(canonical(record))
        self.assertError("integrity", self.manager.resolve, first["reference"])
        self.assertError("integrity", self.manager.export, first["reference"], self.root / "csv-audit")
        self.assertFalse((self.root / "escaped").exists())

    def test_failed_draft_creation_leaves_no_partial_package(self):
        with patch.object(fs, "rename_absent", side_effect=OSError("injected draft commit failure")):
            self.assertError("io", self.manager.create_draft, "local", "other", skill_files("other"))
        self.assertFalse((self.source / "other").exists())
        self.assertEqual(["csv-audit"], [path.name for path in self.source.iterdir()])


class CodecTests(unittest.TestCase):
    def test_strict_json_and_raw_bytes_identity(self):
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', '"\\ud800"'):
            with self.subTest(text=text), self.assertRaises(SkillError):
                loads(text)
        with self.assertRaises(SkillError):
            loads('{"x":1}'.encode("utf-16"))
        with self.assertRaises(SkillError):
            canonical({1: "non-string"})
        self.assertEqual(b'{"a":"\xc3\xa9","b":true}', canonical({"b": True, "a": "\u00e9"}))

    def test_error_contract(self):
        error = SkillError("uncertain", "lost receipt", uncertain=True, operation_id="id", recovery={"reference": "r"})
        self.assertEqual({"code": "uncertain", "message": "lost receipt", "uncertain": True,
                          "operation_id": "id", "recovery": {"reference": "r"}}, error.to_dict())


if __name__ == "__main__":
    unittest.main()
