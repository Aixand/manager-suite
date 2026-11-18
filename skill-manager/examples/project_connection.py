"""Verify project delivery in temporary storage without providers or network."""
import json
from pathlib import Path
import tempfile
from skill_manager import FileContent, Manager


def main():
    with tempfile.TemporaryDirectory(prefix="skill-project-example-") as temporary:
        root = Path(temporary)
        publisher = root / "bundle" / "review"
        publisher.mkdir(parents=True)
        instruction = b"---\nname: review\ndescription: Review proposed changes.\n---\nCheck the changes.\n"
        (publisher / "SKILL.md").write_bytes(instruction)
        (publisher / "assets").mkdir()
        binary = b"\x00\xff\x01"
        (publisher / "assets" / "sample.bin").write_bytes(binary)
        (publisher / "empty").mkdir()
        script = publisher / "check.py"
        script.write_bytes(b"print('review only')\n")
        script.chmod(0o755)
        manager = Manager(root / "store")
        skill = manager.skill("example-app", "review")
        captured = skill.register(publisher, publisher_revision=1).content
        project_root = root / "project"
        project_root.mkdir()
        project = manager.project(project_root)
        plan = project.preview_connect([skill.reference], skills_dir="skills")
        assert not (project_root / ".skill-manager").exists()
        skill.save({"notes.md": FileContent(b"Check failure paths.\n")},
                   expected_revision=captured.revision)
        receipt = project.connect(json.loads(json.dumps(plan)))
        assert receipt["status"] == "completed"
        installed = project_root / "skills" / "review"
        assert (installed / "SKILL.md").read_bytes() == instruction
        assert (installed / "assets" / "sample.bin").read_bytes() == binary
        assert (installed / "empty").is_dir()
        assert (installed / "check.py").stat().st_mode & 0o111
        assert not (installed / "notes.md").exists()
        assert project.connections()["connections"][0]["update_available"]
        updated = project.apply_update(project.preview_update())
        assert updated["status"] == "completed"
        assert (installed / "notes.md").read_bytes() == b"Check failure paths.\n"
        detached = project.apply(project.preview_disconnect([skill.reference]))
        assert detached["status"] == "completed"
        assert not installed.exists()
        assert skill.resolve().read_file("assets/sample.bin") == binary
        print(json.dumps({"status": "completed", "preview_pinned": True,
                          "binary_preserved": True, "detached": True}))


if __name__ == "__main__":
    main()
