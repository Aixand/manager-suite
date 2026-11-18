"""Verify customized publisher updates and retained content after ingestion failure."""
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from prompt_manager import Manager, PromptError


def main():
    with TemporaryDirectory(prefix="prompt-publisher-") as temporary:
        root = Path(temporary)
        source = root / "publisher"
        source.mkdir()
        manifest = {
            "schema": "prompt-manager/pack-1", "name": "review",
            "sections": {"body": {"file": "body.md"}},
            "prompts": {"review": {"sections": [{"id": "body", "section": "body"}]}},
        }
        (source / "pack.json").write_text(json.dumps(manifest), encoding="utf-8")
        body = source / "body.md"
        body.write_text("Publisher one.", encoding="utf-8")
        manager = Manager(home=root / "store", packs=[])
        package = manager.package("publisher", "default")
        first = package.register(source, publisher_revision=1, prompt_id="review")
        saved = package.save({"body.md": "My instructions."}, expected_revision=first.status["revision"])
        body.write_text("Publisher two.", encoding="utf-8")
        pending = package.register(source, publisher_revision=2, prompt_id="review")
        assert pending.status["outcome"] == "update_pending"
        assert pending.status["reconciliation_required"]
        assert "My instructions." in pending.content
        preview = package.preview_reconciliation()
        assert preview["conflicts"]
        # A stale save is rejected even though its body would otherwise be valid.
        try:
            package.save({"body.md": "Stale edit."}, expected_revision=saved["revision"])
        except PromptError as error:
            assert error.code == "conflict"
        else:
            raise AssertionError("Expected a stale-revision conflict")
        accepted = package.reconcile(expected_revision=preview["revision"], take="managed")
        assert not accepted["reconciliation_required"]
        assert "My instructions." in package.render("review")["output"]
        shutil.rmtree(source)
        retained = package.register(source, publisher_revision=3, prompt_id="review")
        assert retained.status["outcome"] == "refresh_failed"
        assert retained.status["sync_error"]
        assert "My instructions." in retained.content
        assert manager.check_store()["ok"]
        print("PASS: edits survive update review, stale writes, and failed ingestion")


if __name__ == "__main__":
    main()
