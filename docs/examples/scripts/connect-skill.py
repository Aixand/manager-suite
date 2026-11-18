"""Register a skill, preview its project connection, then apply the saved plan."""
import json
import tempfile
from pathlib import Path

from skill_manager import Manager


def main():
    with tempfile.TemporaryDirectory(prefix="manager-skill-") as temporary:
        root = Path(temporary)
        publisher = root / "bundle" / "review"
        publisher.mkdir(parents=True)
        content = b"---\nname: review\ndescription: Review code changes.\n---\nCheck missing tests.\n"
        (publisher / "SKILL.md").write_bytes(content)
        manager = Manager(root / "library")
        skill = manager.skill("example-app", "review")
        registered = skill.register(publisher, publisher_revision=1)
        project_root = root / "project"
        project_root.mkdir()
        project = manager.project(project_root)
        plan = project.preview_connect([skill.reference], skills_dir="skills")
        assert not (project_root / "skills" / "review").exists()
        project.connect(plan)
        assert (project_root / "skills" / "review" / "SKILL.md").read_bytes() == content
        assert registered.content.read_file("SKILL.md") == content
        print(json.dumps({"preview_changed_files": False, "connected": True,
                          "skills_dir": "skills", "source_unchanged": (publisher / "SKILL.md").read_bytes() == content}))


if __name__ == "__main__":
    main()
