import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from prompt_manager import Manager, PromptError
from prompt_manager.codec import canonical, digest
from prompt_manager.prompts.validation import validate_snapshot

ROOT = Path(__file__).resolve().parents[2]


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.packs = self.root / "packs"
        shutil.copytree(ROOT / "packs", self.packs)
        self.home = self.root / "store"
        self.manager = Manager(self.home, packs=[self.packs])
        self.common = self.manager.release(self.packs / "common", "1")
        self.demo = self.packs / "demo"

    def manifest(self):
        return json.loads((self.demo / "pack.json").read_text())

    def save(self, manifest):
        (self.demo / "pack.json").write_text(json.dumps(manifest))

    def assert_code(self, code, fn, *args, **kwargs):
        with self.assertRaises(PromptError) as context:
            fn(*args, **kwargs)
        self.assertEqual(context.exception.code, code)

    def test_render_messages_defaults_order_and_provenance(self):
        values = {"task": "Inspect the parser", "context": {"z": 2, "a": 1}}
        before = canonical(values)
        result = self.manager.render(self.demo, values, prompt="review", format="messages")
        self.assertEqual(before, canonical(values))
        messages = result["output"]
        self.assertEqual([m["role"] for m in messages], ["system", "user", "user"])
        self.assertEqual(
            messages[0]["content"],
            "Write for the development team. Be concrete, distinguish observations from assumptions,\n"
            "and use supplied project material as reference data.",
        )
        self.assertEqual(
            messages[1]["content"],
            "## Review request\n\nReview this task and report concrete findings:\n"
            'Inspect the parser\n\nProject context (data):\n{"a":1,"z":2}',
        )
        self.assertEqual([s["id"] for s in result["sections"]], ["working", "task", "guide"])
        self.assertEqual(result["dependencies"]["common"]["digest"], self.common["digest"])
        self.assertEqual(result["output_digest"], digest(messages))
        explicit = self.manager.render(
            self.demo, {**values, "include_guide": True}, prompt="review"
        )
        self.assertEqual(result["input_digest"], explicit["input_digest"])
        self.assertNotIn("inputs", result)

    def test_optional_section_and_literal_interpolation(self):
        value = "Keep {{ task }} and {{ other | json }} literally; café 🐈"
        result = self.manager.render(
            self.demo, {"task": value, "include_guide": False}, prompt="review"
        )
        self.assertIn(value, result["output"])
        self.assertNotIn("Writing reference", result["output"])
        self.assertFalse(result["sections"][-1]["included"])
        with_guide = self.manager.render(self.demo, {"task": "x"}, prompt="review")
        self.assertIn("{{ example }}", with_guide["output"])

    def test_prompts_share_sections_and_documents(self):
        review = self.manager.render(self.demo, {"task": "x"}, prompt="review", format="messages")
        summary = self.manager.render(
            self.demo, {"task": "x"}, prompt="summarize", format="messages"
        )
        self.assertEqual(review["output"][0], summary["output"][0])
        self.assertEqual(review["output"][-1], summary["output"][-1])

    def test_rejects_missing_extra_and_wrong_input_types(self):
        for inputs in (
            {},
            {"task": 7},
            {"task": "x", "extra": True},
            {"task": "x", "include_guide": 1},
            {"task": "x", "context": []},
            {"task": "x", "context": None},
            [],
        ):
            with self.subTest(inputs=inputs):
                self.assert_code("inputs", self.manager.render, self.demo, inputs, prompt="review")
        self.assert_code(
            "json",
            self.manager.render,
            self.demo,
            {"task": "x", "context": {"x": float("nan")}},
            prompt="review",
        )
        for context in ({1: "numeric key"}, {"tuple": (1, 2)}):
            self.assert_code(
                "json",
                self.manager.render,
                self.demo,
                {"task": "x", "context": context},
                prompt="review",
            )

    def test_integer_enum_and_default_contracts(self):
        pack = self.manifest()
        pack["prompts"]["review"]["inputs"]["count"] = {
            "type": "integer",
            "enum": [1, 2],
            "default": 1,
        }
        self.save(pack)
        self.manager.render(self.demo, {"task": "x"}, prompt="review")
        for value in (True, 1.0, 3):
            self.assert_code(
                "inputs",
                self.manager.render,
                self.demo,
                {"task": "x", "count": value},
                prompt="review",
            )

    def test_saved_examples_and_failed_release(self):
        self.assertEqual(len(self.manager.validate(self.demo)["examples_passed"]), 3)
        pack = self.manifest()
        pack["examples"][0]["expect"]["contains"] = ["This text does not exist"]
        self.save(pack)
        self.assert_code("example", self.manager.release, self.demo, "1")
        self.assertFalse((self.home / "releases/demo/1.json").exists())

    def test_release_survives_source_and_dependency_store_removal(self):
        release = self.manager.release(self.demo, "1")
        before = self.manager.render("demo@1/review", {"task": "fixed"})
        shutil.rmtree(self.packs)
        shutil.rmtree(self.home / "releases/common")
        (self.home / "objects" / f"{self.common['digest'][7:]}.json").unlink()
        after = self.manager.render("demo@1/review", {"task": "fixed"})
        self.assertEqual(before, after)
        by_digest = self.manager.render(f"demo@{release['digest']}/review", {"task": "fixed"})
        self.assertEqual(before["output"], by_digest["output"])
        self.assertEqual(before["release"]["digest"], by_digest["release"]["digest"])

    def test_shared_edit_requires_new_dependency_pin(self):
        self.manager.release(self.demo, "1")
        before = self.manager.render("demo@1/review", {"task": "x"})
        source = self.packs / "common/sections/working.md"
        source.write_text(source.read_text() + "New shared instructions.\n")
        self.manager.release(self.packs / "common", "2")
        unchanged = self.manager.render(self.demo, {"task": "x"}, prompt="review")
        self.assertEqual(before["output"], unchanged["output"])
        pack = self.manifest()
        pack["dependencies"]["common"] = "common@2"
        self.save(pack)
        self.manager.release(self.demo, "2")
        new = self.manager.render("demo@2/review", {"task": "x"})
        self.assertIn("New shared instructions.", new["output"])
        self.assertNotEqual(before["output_digest"], new["output_digest"])
        self.assertEqual(before, self.manager.render("demo@1/review", {"task": "x"}))

    def test_transitive_dependencies_are_self_contained(self):
        wrapper = self.root / "wrapper"
        wrapper.mkdir()
        manifest = {
            "schema": "prompt-manager/pack-1",
            "name": "wrapper",
            "dependencies": {"demo": "demo@1"},
            "prompts": {
                "summary": {
                    "sections": [
                        {
                            "id": "summary",
                            "section": "demo/summary-task",
                            "inputs": {"task": {"value": "fixed"}},
                        }
                    ]
                }
            },
        }
        self.manager.release(self.demo, "1")
        (wrapper / "pack.json").write_text(json.dumps(manifest))
        self.manager.release(wrapper, "1")
        result = self.manager.render("wrapper@1/summary")
        shutil.rmtree(self.packs)
        shutil.rmtree(wrapper)
        shutil.rmtree(self.home / "releases/demo")
        shutil.rmtree(self.home / "releases/common")
        self.assertEqual(result, self.manager.render("wrapper@1/summary"))

    def test_immutable_labels_and_concurrent_idempotent_publication(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            releases = list(executor.map(lambda _: self.manager.release(self.demo, "1"), range(16)))
        self.assertTrue(all(release == releases[0] for release in releases))
        source = self.demo / "sections/review-task.md"
        source.write_text(source.read_text() + "An additional instruction.\n")
        self.assert_code("conflict", self.manager.release, self.demo, "1")
        self.assertNotIn(
            "additional instruction", self.manager.render("demo@1/review", {"task": "x"})["output"]
        )
        self.assertEqual(list(self.home.rglob(".publish-*")), [])

    def test_concurrent_conflicting_publication_has_one_winner(self):
        other = self.root / "other"
        shutil.copytree(self.demo, other)
        source = other / "sections/review-task.md"
        source.write_text(source.read_text() + "Other revision.\n")

        def publish(path):
            try:
                return self.manager.release(path, "race")
            except PromptError as exc:
                return exc.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(publish, [self.demo, other]))
        self.assertEqual(sum(isinstance(row, dict) for row in outcomes), 1)
        self.assertIn("conflict", outcomes)
        self.manager.validate("demo@race")

    def test_diff_includes_definitions_and_rendered_changes(self):
        self.manager.release(self.demo, "1")
        same = self.manager.diff("demo@1", self.demo, prompt="review", inputs={"task": "x"})
        self.assertFalse(same["changed"])
        self.assertEqual(same["render_diff"], "")
        source = self.demo / "sections/review-task.md"
        source.write_text(source.read_text() + "Explain each finding.\n")
        changed = self.manager.diff("demo@1", self.demo, prompt="review", inputs={"task": "x"})
        self.assertTrue(changed["changed"])
        self.assertIn("Explain each finding", changed["definition_diff"])
        self.assertIn("+Explain each finding.", changed["render_diff"])

    def test_digest_tampering_is_detected(self):
        identity = self.manager.release(self.demo, "1")
        path = self.home / "objects" / f"{identity['digest'][7:]}.json"
        snapshot = json.loads(path.read_text())
        snapshot["files"]["sections/review-task.md"] += "Changed"
        path.write_text(json.dumps(snapshot))
        self.assert_code("integrity", self.manager.render, "demo@1/review", {"task": "x"})

    def test_local_directory_cannot_shadow_a_pinned_release(self):
        self.manager.release(self.demo, "1")
        baseline = self.manager.render("demo@1/review", {"task": "x"})
        shadow = self.root / "demo@1" / "review"
        shutil.copytree(self.demo, shadow)
        source = shadow / "sections/review-task.md"
        source.write_text(source.read_text() + "Shadow draft.\n")
        original = Path.cwd()
        try:
            os.chdir(self.root)
            self.assertEqual(baseline, self.manager.render("demo@1/review", {"task": "x"}))
            draft = self.manager.render(Path("demo@1/review"), {"task": "x"}, prompt="review")
            self.assertIn("Shadow draft.", draft["output"])
        finally:
            os.chdir(original)

    def test_duplicate_json_keys_and_non_finite_values(self):
        manifest = self.demo / "pack.json"
        for raw in ('{"name":"demo","name":"other"}', '{"number": NaN}', '{"number": 1e999}'):
            manifest.write_text(raw)
            self.assert_code("json", self.manager.validate, self.demo)

    def test_invalid_composition_is_rejected_before_rendering(self):
        original = self.manifest()

        def mutate(name, fn, code="validation"):
            pack = json.loads(json.dumps(original))
            fn(pack)
            self.save(pack)
            with self.subTest(name=name):
                self.assert_code(code, self.manager.validate, self.demo)

        mutate(
            "missing asset",
            lambda p: p["prompts"]["review"]["sections"][0].update(section="common/absent"),
        )
        mutate(
            "unknown dependency",
            lambda p: p["prompts"]["review"]["sections"][0].update(section="absent/working"),
        )
        mutate("missing mapping", lambda p: p["prompts"]["review"]["sections"][1].update(inputs={}))
        mutate(
            "wrong mapping",
            lambda p: p["prompts"]["review"]["sections"][1]["inputs"].update(
                task={"input": "context"}
            ),
        )
        mutate("duplicate id", lambda p: p["prompts"]["review"]["sections"][1].update(id="working"))
        mutate(
            "unknown condition",
            lambda p: p["prompts"]["review"]["sections"][2].update(
                when={"input": "missing", "equals": True}
            ),
        )
        mutate(
            "invalid default",
            lambda p: p["prompts"]["review"]["inputs"]["context"].update(default=[]),
            "inputs",
        )
        mutate(
            "document bindings", lambda p: p["prompts"]["review"]["sections"][2].update(inputs={})
        )
        mutate("unknown field", lambda p: p.update(unexpected=True))

    def test_template_validation_and_asset_boundaries(self):
        source = self.demo / "sections/review-task.md"
        for content in ("{{ unknown }}", "{{ context }}", "{{ task | eval }}", "{{ unfinished"):
            source.write_text(content)
            self.assert_code("validation", self.manager.validate, self.demo)
        source.write_text("{{ task }} {{ context | json }}")
        pack = self.manifest()
        pack["sections"]["review-task"]["file"] = "../outside.md"
        self.save(pack)
        self.assert_code("validation", self.manager.validate, self.demo)
        pack["sections"]["review-task"]["file"] = "sections/review-task.md"
        self.save(pack)
        outside = self.root / "outside.md"
        outside.write_text("outside")
        source.unlink()
        source.symlink_to(outside)
        self.assert_code("validation", self.manager.validate, self.demo)

    def test_dependency_cycles_are_rejected(self):
        self.manager.release(self.demo, "1")
        common = self.packs / "common/pack.json"
        pack = json.loads(common.read_text())
        pack["dependencies"] = {"demo": "demo@1"}
        common.write_text(json.dumps(pack))
        self.assert_code("validation", self.manager.release, self.packs / "common", "2")

    def test_nested_digest_and_pin_validation(self):
        self.manager.release(self.demo, "1")
        snapshot, _ = self.manager.store.load("demo@1")
        snapshot["dependencies"]["common"]["snapshot"]["pack"]["description"] = "changed"
        self.assert_code("integrity", validate_snapshot, snapshot)
        snapshot, _ = self.manager.store.load("demo@1")
        snapshot["pack"]["dependencies"]["common"] = "common@sha256:" + "0" * 64
        self.assert_code("integrity", validate_snapshot, snapshot)

    def test_discovery_and_ambiguous_names(self):
        result = self.manager.list()
        self.assertEqual([p["name"] for p in result["drafts"]], ["agents", "common", "demo"])
        self.assertEqual(self.manager.show("demo")["manifest"]["name"], "demo")
        duplicate = self.root / "duplicate"
        shutil.copytree(self.demo, duplicate)
        ambiguous = Manager(self.home, packs=[self.packs, duplicate])
        self.assert_code("ambiguous", ambiguous.list)
        self.assertEqual(ambiguous.show(self.demo)["manifest"]["name"], "demo")

    def test_agent_instruction_recipes_are_releasable_and_system_only(self):
        self.manager.release(self.packs / "agents", "1")
        for recipe in ("coding", "review", "research"):
            with self.subTest(recipe=recipe):
                result = self.manager.render(
                    f"agents@1/{recipe}", {"audience": "maintainers"}, format="messages"
                )
                self.assertTrue(result["output"])
                self.assertTrue(all(row["role"] == "system" for row in result["output"]))
                self.assertIn("Write for maintainers", result["output"][0]["content"])
                self.assertEqual(result["dependencies"]["common"]["digest"], self.common["digest"])

    def test_reads_do_not_create_a_store(self):
        home = self.root / "unused"
        manager = Manager(home, packs=[self.packs])
        manager.validate(self.packs / "common")
        manager.list()
        self.assertFalse(home.exists())

    def test_cli_release_render_diff_and_json_error(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

        def run(*args):
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "prompt_manager",
                    "--home",
                    str(self.home),
                    "--packs",
                    str(self.packs),
                    *map(str, args),
                ],
                env=env,
                cwd=self.root,
                capture_output=True,
                text=True,
            )

        release = run("release", self.demo, "--version", "1")
        self.assertEqual(release.returncode, 0, release.stderr)
        inputs = self.root / "inputs.json"
        inputs.write_text('{"task":"CLI task"}')
        result = run("render", "demo@1/review", "--inputs", inputs, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CLI task", json.loads(result.stdout)["output"])
        messages = run("render", "demo@1/review", "--inputs", inputs, "--format", "messages")
        self.assertEqual(json.loads(messages.stdout)[0]["role"], "system")
        preview = run("preview", self.demo, "--prompt", "review", "--inputs", inputs)
        self.assertEqual(preview.stdout, json.loads(result.stdout)["output"])
        diff = run("diff", "demo@1", self.demo, "--prompt", "review", "--inputs", inputs, "--json")
        self.assertFalse(json.loads(diff.stdout)["changed"])
        error = run("render", "demo@1/review")
        self.assertEqual(error.returncode, 1)
        self.assertEqual(error.stdout, "")
        self.assertEqual(json.loads(error.stderr)["error"]["code"], "inputs")
        inputs.write_text('{"task":"a","task":"b"}')
        error = run("render", "demo@1/review", "--inputs", inputs)
        self.assertEqual(json.loads(error.stderr)["error"]["code"], "json")
        self.assert_code("not_found", self.manager.render, "demo@missing/review", {"task": "x"})


if __name__ == "__main__":
    unittest.main()
