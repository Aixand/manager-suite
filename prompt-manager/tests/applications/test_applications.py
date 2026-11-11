import concurrent.futures
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_manager import UNSET, Manager, PromptError
from prompt_manager.codec import digest
from prompt_manager.library.snapshots import load
from prompt_manager.prompts.rendering import render as render_snapshot


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manager = Manager(self.root / "store", packs=[])
        self.alpha = self.make_pack("alpha", "Alpha")
        self.beta = self.make_pack("beta", "Beta")
        self.manager.register_source(
            "alpha", [self.alpha], owner={"application": "alpha"}, expected_revision=None
        )
        self.manager.register_source("beta", [self.beta], expected_revision=None)

    def make_pack(self, directory, text):
        path = self.root / directory
        path.mkdir(parents=True)
        (path / "rules.md").write_text(text + ": {{ context | json }}", encoding="utf-8")
        (path / "pack.json").write_text(
            json.dumps(
                {
                    "schema": "prompt-manager/pack-1",
                    "name": "shared",
                    "sections": {
                        "rules": {"file": "rules.md", "inputs": {"context": {"type": "object"}}}
                    },
                    "prompts": {
                        "review": {
                            "inputs": {"context": {"type": "object", "default": {}}},
                            "sections": [
                                {
                                    "id": "rules",
                                    "section": "rules",
                                    "role": "system",
                                    "inputs": {"context": {"input": "context"}},
                                }
                            ],
                        }
                    },
                    "examples": [
                        {
                            "name": "context",
                            "prompt": "review",
                            "inputs": {"context": {"n": 1}},
                            "expect": {"contains": ['"n":1'], "section_ids": ["rules"]},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def draft(self, source="alpha", path=None):
        return {
            "kind": "draft",
            "source_id": source,
            "pack_path": str(path or getattr(self, source)),
            "prompt": "review",
        }

    @staticmethod
    def pinned(version="1"):
        return {"kind": "release", "reference": f"shared@{version}/review"}

    def register(self, identifier="alpha-app", selection=None, **kwargs):
        return self.manager.register_application(
            identifier, prompts={"review": selection or self.draft()}, **kwargs
        )

    def assert_code(self, code, action, *args, **kwargs):
        with self.assertRaises(PromptError) as raised:
            action(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def test_two_independent_applications_and_colliding_names(self):
        alpha = self.register()
        beta = self.register("beta-app", self.draft("beta"))
        context = {"text": "{{ literal }}", "items": ["complete", {"value": 2}]}
        a = self.manager.render_application("alpha-app", "review", {"context": context})
        b = self.manager.render_application("beta-app", "review", {"context": context})
        self.assertIn("Alpha", a["output"])
        self.assertIn("Beta", b["output"])
        self.assertIn("{{ literal }}", a["output"])
        self.assertEqual(a["resolution"]["application_revision"], alpha["revision"])
        self.assertEqual(b["resolution"]["application_revision"], beta["revision"])
        self.assertEqual(a["resolution"]["source_id"], "alpha")
        self.assertEqual(a["release"], a["resolution"]["release"])
        self.assertEqual(a["dependencies"], a["resolution"]["dependencies"])
        self.assertNotEqual(
            a["resolution"]["catalog_entry_id"], b["resolution"]["catalog_entry_id"]
        )
        catalog = self.manager.catalog()
        self.assertEqual(len(catalog["packs"]), 2)
        self.assertEqual(catalog["applications"][0]["resolutions"]["review"], a["resolution"])
        self.assertEqual(
            [row["id"] for row in self.manager.applications()], ["alpha-app", "beta-app"]
        )
        self.assertFalse((self.manager.store.home / "objects").exists())

    def test_same_path_sources_keep_catalog_identity_and_selected_source_metadata(self):
        self.manager.register_source("alias", [self.alpha], writable=False, expected_revision=None)
        self.register("alias-app", self.draft("alias", self.alpha))
        self.register()
        a = self.manager.resolve_application("alpha-app", "review")
        alias = self.manager.resolve_application("alias-app", "review")
        self.assertEqual(a["catalog_entry_id"], alias["catalog_entry_id"])
        self.assertEqual(alias["source_id"], "alias")
        self.assertFalse(alias["editable"])
        self.assertTrue(a["editable"])
        self.assertEqual(len(self.manager.catalog()["packs"]), 2)
        self.assertEqual(
            a["catalog_entry_id"], digest([a["store_id"], "draft", str(self.alpha)])[7:]
        )

    def test_save_publish_and_advance_are_separate_and_pins_survive(self):
        entry = next(row for row in self.manager.catalog()["packs"] if row["source_id"] == "alpha")
        release = self.manager.release_catalog(entry["id"], "1", expected_digest=entry["digest"])
        app = self.register(selection=self.pinned())
        other = self.register("other-app", self.pinned())
        draft = self.register("draft-app")
        old = self.manager.render_application("alpha-app", "review")
        release_bytes = (self.manager.store.home / "releases/shared/1.json").read_bytes()
        updated = self.manager.save_catalog_file(
            entry["id"],
            "rules.md",
            "New Alpha: {{ context | json }}",
            expected_digest=entry["digest"],
        )
        self.assert_code(
            "conflict",
            self.manager.save_catalog_file,
            entry["id"],
            "rules.md",
            "bad",
            expected_digest=entry["digest"],
        )
        self.assert_code(
            "conflict",
            self.manager.release_catalog,
            entry["id"],
            "2",
            expected_digest=entry["digest"],
        )
        self.assertEqual(self.manager.render_application("alpha-app", "review"), old)
        self.assertIn("New Alpha", self.manager.render_application("draft-app", "review")["output"])
        second = self.manager.release_catalog(
            entry["id"], "2", expected_digest=updated["entry"]["digest"]
        )
        self.assertNotEqual(release["digest"], second["digest"])
        self.assertEqual(self.manager.application("alpha-app"), app)
        self.assertEqual(self.manager.application("draft-app"), draft)
        self.assertEqual(self.manager.application("other-app"), other)
        self.assertEqual(self.manager.render_application("alpha-app", "review"), old)
        advanced = self.manager.set_application_prompt(
            "alpha-app", "review", self.pinned("2"), expected_revision=app["revision"]
        )
        self.assertNotEqual(advanced["revision"], app["revision"])
        self.assertIn("New Alpha", self.manager.render_application("alpha-app", "review")["output"])
        self.assertEqual(
            self.manager.render_application("other-app", "review")["output"], old["output"]
        )
        self.assertEqual(
            (self.manager.store.home / "releases/shared/1.json").read_bytes(), release_bytes
        )
        self.assert_code(
            "conflict",
            self.manager.release_catalog,
            entry["id"],
            "1",
            expected_digest=updated["entry"]["digest"],
        )

    def test_registration_revision_guards_and_concurrent_mapping_updates(self):
        app = self.register()
        self.assert_code("conflict", self.register)
        self.assert_code(
            "conflict",
            self.manager.set_application_prompt,
            "alpha-app",
            "review",
            None,
            expected_revision=None,
        )

        def change(version):
            try:
                self.manager.set_application_prompt(
                    "alpha-app", "review", self.pinned(version), expected_revision=app["revision"]
                )
                return "changed"
            except PromptError as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(change, ["2", "3"])), ["changed", "conflict"])
        self.assert_code(
            "conflict",
            self.manager.unregister_application,
            "alpha-app",
            expected_revision=app["revision"],
        )
        current = self.manager.application("alpha-app")
        removed = self.manager.set_application_prompt(
            "alpha-app", "review", None, expected_revision=current["revision"]
        )
        self.assertEqual(removed["prompts"], {})
        replaced = self.manager.register_application(
            "alpha-app", prompts={}, label="Updated", expected_revision=removed["revision"]
        )
        self.assertEqual(replaced["label"], "Updated")
        self.assertEqual(
            self.manager.unregister_application(
                "alpha-app", expected_revision=replaced["revision"]
            ),
            replaced,
        )
        self.assert_code("not_found", self.manager.application, "alpha-app")

    def test_guarded_sources_and_legacy_result_shape(self):
        legacy = self.manager.register_source("legacy", [self.alpha])
        self.assertEqual(set(legacy), {"schema", "id", "label", "paths"})
        self.assertIsNot(UNSET, None)
        current = next(row for row in self.manager.sources() if row["id"] == "legacy")
        self.assertTrue(current["writable"])
        self.assertIsNone(current["owner"])
        self.assert_code(
            "conflict", self.manager.register_source, "legacy", [self.alpha], expected_revision=None
        )
        changed = self.manager.register_source(
            "legacy",
            [self.alpha],
            writable=False,
            owner="vendor",
            expected_revision=current["revision"],
        )
        self.assertNotEqual(changed["revision"], current["revision"])
        self.assert_code(
            "conflict",
            self.manager.unregister_source,
            "legacy",
            expected_revision=current["revision"],
        )
        self.manager.unregister_source("legacy", expected_revision=changed["revision"])
        self.assertNotIn("legacy", [row["id"] for row in self.manager.sources()])
        self.manager.register_source("legacy", [self.beta])
        self.manager.register_source("legacy", [self.alpha])  # Retained unguarded upsert.
        self.manager.unregister("sources", "legacy")

    def test_readonly_catalog_prevents_source_edits_and_metadata_changes_apply(self):
        current = self.manager.sources()[0]
        readonly = self.manager.register_source(
            "alpha", [self.alpha], writable=False, expected_revision=current["revision"]
        )
        self.register()
        resolution = self.manager.resolve_application("alpha-app", "review")
        entry = next(row for row in self.manager.catalog()["packs"] if row["source_id"] == "alpha")
        self.assertFalse(resolution["editable"])
        self.assertFalse(entry["editable"])
        original = (self.alpha / "rules.md").read_bytes()
        self.assert_code(
            "conflict",
            self.manager.save_catalog_file,
            entry["id"],
            "rules.md",
            "changed",
            expected_digest=entry["digest"],
        )
        self.assertEqual((self.alpha / "rules.md").read_bytes(), original)
        self.manager.register_source(
            "alpha", [self.alpha], writable=True, expected_revision=readonly["revision"]
        )
        self.assertTrue(self.manager.resolve_application("alpha-app", "review")["editable"])

    def test_unavailable_and_invalid_configured_targets_never_fall_back(self):
        self.register(fallback_policy="packaged")
        fallback = {"pack_path": str(self.beta), "prompt": "review"}
        shutil.rmtree(self.alpha)
        self.assert_code(
            "not_found", self.manager.render_application, "alpha-app", "review", fallback=fallback
        )
        catalog = self.manager.catalog()
        self.assertEqual(catalog["sources"][0]["status"], "unavailable")
        self.assertEqual(
            catalog["applications"][0]["resolutions"]["review"]["error"]["code"], "not_found"
        )
        self.assertEqual(len(catalog["packs"]), 1)
        self.alpha = self.make_pack("alpha", "Alpha")
        (self.alpha / "rules.md").write_text("{{ unknown }}")
        self.assert_code(
            "validation", self.manager.render_application, "alpha-app", "review", fallback=fallback
        )
        self.assertEqual(self.manager.catalog()["packs"][0]["status"], "invalid")

    def test_missing_removed_source_and_containment_fail_visibly(self):
        self.register()
        current = self.manager.sources()[0]
        self.manager.unregister_source("alpha", expected_revision=current["revision"])
        self.assert_code("not_found", self.manager.resolve_application, "alpha-app", "review")
        self.manager.register_source("alpha", [self.beta])
        self.assert_code("conflict", self.manager.resolve_application, "alpha-app", "review")
        self.assertTrue(self.alpha.is_dir())
        (self.manager.store.home / "sources/alpha.json").write_text("[]")
        self.assert_code("validation", self.manager.resolve_application, "alpha-app", "review")

    def test_pack_path_symlink_retargeting_is_not_silent(self):
        app = self.register()
        source = self.manager.sources()[0]
        relocated = self.root / "relocated"
        self.alpha.rename(relocated)
        self.alpha.symlink_to(self.beta, target_is_directory=True)
        self.assert_code("conflict", self.manager.resolve_application, "alpha-app", "review")
        self.assertEqual(self.manager.application("alpha-app"), app)
        self.assertEqual(self.manager.sources()[0]["revision"], source["revision"])
        changed = self.manager.set_application_prompt(
            "alpha-app", "other", self.draft("beta"), expected_revision=app["revision"]
        )
        self.assertEqual(changed["prompts"]["review"], app["prompts"]["review"])
        self.assert_code("conflict", self.manager.resolve_application, "alpha-app", "review")
        self.manager.set_application_prompt(
            "alpha-app", "review", self.draft("beta"), expected_revision=changed["revision"]
        )
        self.assertIn("Beta", self.manager.render_application("alpha-app", "review")["output"])
        self.manager.unregister_source("alpha", expected_revision=source["revision"])

    def test_explicit_packaged_fallback_and_policy_are_visible_without_registration(self):
        home = self.root / "absent-store"
        manager = Manager(home, packs=[])
        fallback = {"pack_path": str(self.alpha), "prompt": "review"}
        self.assert_code("not_found", manager.resolve_application, "missing", "review")
        result = manager.render_application(
            "missing", "review", fallback=fallback, format="messages"
        )
        self.assertEqual(
            result["output"],
            manager.render(self.alpha, prompt="review", format="messages")["output"],
        )
        resolution = result["resolution"]
        for key in ("application_revision", "source_id", "catalog_entry_id", "reference"):
            self.assertIsNone(resolution[key])
        self.assertFalse(resolution["editable"])
        self.assertTrue(resolution["used_fallback"])
        self.assertEqual(resolution["target"]["kind"], "packaged")
        manager.catalog()
        manager.sources()
        manager.applications()
        self.assertFalse(home.exists())
        manager.register_application("missing", prompts={})
        self.assert_code(
            "not_found", manager.resolve_application, "missing", "review", fallback=fallback
        )
        app = manager.application("missing")
        manager.register_application(
            "missing", prompts={}, fallback_policy="packaged", expected_revision=app["revision"]
        )
        self.assertTrue(
            manager.resolve_application("missing", "review", fallback=fallback)["used_fallback"]
        )

    def test_release_and_digest_pins_survive_source_and_registration_removal(self):
        published = self.manager.release(self.alpha, "1")
        self.register(selection=self.pinned())
        self.register("digest-app", self.pinned(published["digest"]))
        old = self.manager.render_application("alpha-app", "review")
        for row in self.manager.sources():
            self.manager.unregister_source(row["id"], expected_revision=row["revision"])
        shutil.rmtree(self.alpha)
        self.assertEqual(self.manager.render_application("alpha-app", "review"), old)
        by_digest = self.manager.render_application("digest-app", "review")
        self.assertEqual(by_digest["output"], old["output"])
        self.assertEqual(by_digest["release"]["digest"], old["release"]["digest"])
        self.assertIsNone(by_digest["resolution"]["catalog_entry_id"])
        self.assertEqual(old["resolution"]["source_id"], "releases")
        obj = self.manager.store.home / "objects" / (published["digest"][7:] + ".json")
        obj.write_text(obj.read_text().replace("Alpha", "Tampered"))
        self.assert_code(
            "integrity",
            self.manager.render_application,
            "alpha-app",
            "review",
            fallback={"pack_path": str(self.beta), "prompt": "review"},
        )

    def test_captured_render_does_not_reread_draft_after_resolution(self):
        self.register()
        before = self.manager.render_application("alpha-app", "review")
        original_load = load
        calls = []

        def load_then_edit(*args, **kwargs):
            captured = original_load(*args, **kwargs)
            calls.append(captured)
            (self.alpha / "rules.md").write_text("Edited during render: {{ context | json }}")
            return captured

        with patch("prompt_manager.applications.resolution.load", side_effect=load_then_edit):
            captured = self.manager.render_application("alpha-app", "review")
        self.assertEqual(len(calls), 1)
        self.assertEqual(captured, before)
        self.assertNotEqual(
            self.manager.render_application("alpha-app", "review")["release"], captured["release"]
        )

    def test_dependency_and_legacy_render_identity_are_retained(self):
        common_manifest = json.loads((self.beta / "pack.json").read_text())
        common_manifest["name"] = "common"
        (self.beta / "pack.json").write_text(json.dumps(common_manifest))
        common = self.manager.release(self.beta, "common")
        manifest = json.loads((self.alpha / "pack.json").read_text())
        manifest["dependencies"] = {"base": "common@common"}
        manifest["prompts"]["review"]["sections"].append(
            {
                "id": "base",
                "section": "base/rules",
                "role": "system",
                "inputs": {"context": {"input": "context"}},
            }
        )
        (self.alpha / "pack.json").write_text(json.dumps(manifest))
        self.register()
        enriched = self.manager.render_application("alpha-app", "review")
        legacy = self.manager.render(self.alpha, prompt="review")
        self.assertEqual(
            set(legacy),
            {
                "schema",
                "renderer",
                "prompt",
                "release",
                "format",
                "output",
                "input_digest",
                "output_digest",
                "sections",
                "dependencies",
            },
        )
        self.assertEqual(legacy["schema"], "prompt-manager/render-result-1")
        self.assertEqual(enriched["schema"], "prompt-manager/application-render-result-1")
        self.assertEqual(
            {k: v for k, v in enriched.items() if k not in {"schema", "resolution"}},
            {k: v for k, v in legacy.items() if k != "schema"},
        )
        self.assertEqual(enriched["resolution"]["dependencies"]["base"]["digest"], common["digest"])
        self.assertEqual(
            set(enriched["resolution"]),
            {
                "schema",
                "application_id",
                "application_revision",
                "logical_prompt",
                "target",
                "store",
                "store_id",
                "source_id",
                "catalog_entry_id",
                "path",
                "reference",
                "editable",
                "release",
                "dependencies",
                "used_fallback",
            },
        )
        snapshot, identity, prompt = load(
            self.manager.store, self.manager.packs, self.alpha, "review"
        )
        self.assertEqual(render_snapshot(snapshot, prompt, {}, identity), legacy)

    def test_invalid_registration_is_isolated_and_invalid_targets_are_rejected(self):
        self.register()
        (self.manager.store.home / "applications/broken.json").write_text("{}")
        self.assertEqual(len(self.manager.applications()), 2)
        self.assertIn("error", self.manager.applications()[1])
        self.assertEqual(len(self.manager.catalog()["packs"]), 2)
        for selection in [
            None,
            [],
            {"kind": "latest"},
            {"kind": "release", "reference": "shared@1"},
            {"kind": "release", "reference": "shared@1/review/extra"},
            {**self.draft(), "pack_path": "relative"},
            {**self.draft(), "extra": True},
        ]:
            with self.subTest(selection=selection):
                self.assert_code(
                    "validation",
                    self.manager.register_application,
                    "invalid",
                    prompts={"review": selection},
                )
        self.assert_code(
            "validation",
            self.manager.register_application,
            "invalid",
            prompts={"Bad Name": self.draft()},
        )
        appfile = self.manager.store.home / "applications/alpha-app.json"
        content = json.loads(appfile.read_text())
        content["label"] = "tampered"
        appfile.write_text(json.dumps(content))
        self.assert_code(
            "integrity",
            self.manager.resolve_application,
            "alpha-app",
            "review",
            fallback={"pack_path": str(self.beta), "prompt": "review"},
        )

    def test_nested_contained_pack_has_no_invented_catalog_id(self):
        nested = self.make_pack("nested/deep/pack", "Nested")
        self.manager.register_source("nested", [self.root / "nested"], expected_revision=None)
        self.register("nested-app", self.draft("nested", nested))
        resolved = self.manager.resolve_application("nested-app", "review")
        self.assertIsNone(resolved["catalog_entry_id"])
        self.assertEqual(resolved["path"], str(nested))
        self.manager.register_source("nested-direct", [nested], expected_revision=None)
        visible = self.manager.resolve_application("nested-app", "review")
        self.assertEqual(visible["source_id"], "nested")
        self.assertEqual(
            visible["catalog_entry_id"], digest([visible["store_id"], "draft", str(nested)])[7:]
        )

    def test_concurrent_source_create_and_metadata_updates_are_guarded(self):
        def register(base, writable):
            try:
                return self.manager.register_source(
                    "race", [self.alpha], writable=writable, expected_revision=base
                )
            except PromptError as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            created = list(pool.map(lambda writable: register(None, writable), [True, False]))
        self.assertEqual(created.count("conflict"), 1)
        winner = next(row for row in created if isinstance(row, dict))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            changed = list(
                pool.map(
                    lambda writable: register(winner["revision"], writable),
                    [not winner["writable"], not winner["writable"]],
                )
            )
        self.assertEqual(changed.count("conflict"), 1)

    def test_release_mapping_does_not_infer_missing_prompt_or_latest_version(self):
        self.register(
            selection={"kind": "release", "reference": "shared@missing/review"},
            fallback_policy="packaged",
        )
        fallback = {"pack_path": str(self.beta), "prompt": "review"}
        self.assert_code(
            "not_found", self.manager.resolve_application, "alpha-app", "review", fallback=fallback
        )
        self.manager.release(self.alpha, "1")
        app = self.manager.application("alpha-app")
        self.manager.set_application_prompt(
            "alpha-app",
            "review",
            {"kind": "release", "reference": "shared@1/unknown"},
            expected_revision=app["revision"],
        )
        self.assert_code(
            "not_found", self.manager.resolve_application, "alpha-app", "review", fallback=fallback
        )

    def test_invalid_utf8_and_nonfinite_registration_data_are_rejected(self):
        for kwargs in (
            {"label": "\ud800"},
            {"label": float("nan")},
            {"prompts": {"review": {**self.draft(), "extra": float("inf")}}},
        ):
            arguments = {"prompts": {"review": self.draft()}, **kwargs}
            with self.assertRaises(PromptError):
                self.manager.register_application("invalid", **arguments)
        self.assertFalse((self.manager.store.home / "applications/invalid.json").exists())

    def test_new_cli_contracts_and_legacy_errors(self):
        def run(*args):
            return subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "prompt_manager",
                    "--home",
                    str(self.manager.store.home),
                    *args,
                ],
                cwd=self.root,
                capture_output=True,
                text=True,
            )

        file = self.root / "mapping.json"
        file.write_text(json.dumps({"review": self.draft()}))
        registered = run("application", "register", "cli-app", "--prompts", "@" + str(file))
        self.assertEqual(registered.returncode, 0, registered.stderr)
        app = json.loads(registered.stdout)
        self.assertEqual(json.loads(run("application", "show", "cli-app").stdout), app)
        self.assertEqual(json.loads(run("application", "list").stdout), [app])
        resolved = json.loads(run("application", "resolve", "cli-app", "review").stdout)
        self.assertEqual(resolved["source_id"], "alpha")
        rendered = run(
            "application",
            "render",
            "cli-app",
            "review",
            "--inputs",
            '{"context":{"literal":"{{ x }}"}}',
            "--json",
        )
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertIn("{{ x }}", json.loads(rendered.stdout)["output"])
        inputs = self.root / "inputs.json"
        inputs.write_text('{"context":{"n":1}}')
        messages = run(
            "application",
            "render",
            "cli-app",
            "review",
            "--inputs",
            "@" + str(inputs),
            "--format",
            "messages",
        )
        self.assertEqual(json.loads(messages.stdout)[0]["role"], "system")
        removed = run(
            "application",
            "set",
            "cli-app",
            "review",
            "--target",
            "null",
            "--expected-revision",
            app["revision"],
        )
        self.assertEqual(removed.returncode, 0, removed.stderr)
        current = json.loads(removed.stdout)
        stale = run("application", "unregister", "cli-app", "--expected-revision", app["revision"])
        self.assertEqual(stale.returncode, 1)
        self.assertEqual(json.loads(stale.stderr)["error"]["code"], "conflict")
        malformed = run("application", "register", "bad", "--prompts", '{"review":{},"review":{}}')
        self.assertEqual(malformed.returncode, 1)
        self.assertEqual(json.loads(malformed.stderr)["error"]["code"], "json")
        unregistered = run(
            "application", "unregister", "cli-app", "--expected-revision", current["revision"]
        )
        self.assertEqual(unregistered.returncode, 0, unregistered.stderr)
        fallback = run(
            "application",
            "resolve",
            "cli-app",
            "review",
            "--fallback",
            json.dumps({"pack_path": str(self.alpha), "prompt": "review"}),
        )
        self.assertTrue(json.loads(fallback.stdout)["used_fallback"])


if __name__ == "__main__":
    unittest.main()
