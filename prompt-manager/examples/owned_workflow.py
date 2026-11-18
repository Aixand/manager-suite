"""Verify snapshot transfer, metadata search, and full-store recovery without providers."""
from pathlib import Path
from tempfile import TemporaryDirectory

from prompt_manager import Manager


def main():
    with TemporaryDirectory(prefix="prompt-owned-") as temporary:
        root = Path(temporary)
        manager = Manager(home=root / "store", packs=[])
        manager.create_managed("review", content="Review carefully.", role="system")
        package = manager.package("local", "review")
        first = package.status()
        tagged = package.set_tags(["Review", " Python "], expected_revision=first["revision"])
        assert tagged["tags"] == ["python", "review"]
        assert manager.search_managed("review", tags=["python"])["items"]
        package.save({"body.md": "Review the parser."}, expected_revision=tagged["revision"])
        original = package.render("review", revision=first["revision"], format="messages")
        assert original["output"] == [{"role": "system", "content": "Review carefully."}]

        exported = root / "snapshot.json"
        package.export(exported)
        transferred = Manager(home=root / "transferred", packs=[])
        transferred.import_local(exported, package_id="portable")
        assert transferred.package("local", "portable").render("review")["output"] == package.render("review")["output"]
        assert len(transferred.package("local", "portable").history()) == 1

        backup = manager.backup_store(root / "backup.zip")
        assert backup["validated"]
        restored = Manager(home=root / "restored", packs=[])
        assert restored.restore_store(root / "backup.zip")["validated"]
        recovered = restored.package("local", "review")
        assert recovered.history() == package.history()
        assert recovered.render("review", revision=first["revision"])["output"] == package.render("review", revision=first["revision"])["output"]
        assert recovered.status()["tags"] == ["python", "review"]
        assert restored.check_store()["ok"]
        print("PASS: snapshot transfer starts history; backup restores history, pins, and tags")


if __name__ == "__main__":
    main()
