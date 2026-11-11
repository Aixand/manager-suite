"""Run an isolated application integration example; no network or model needed."""

import json
import tempfile
from pathlib import Path

from skill_manager import FileContent, Manager


def main():
    with tempfile.TemporaryDirectory(prefix="skill-sdk-example-") as temporary:
        root = Path(temporary)
        publisher = root / "bundle" / "code-review"
        publisher.mkdir(parents=True)
        original = b"---\nname: code-review\ndescription: Review changes before merging.\n---\nReview carefully.\n"
        (publisher / "SKILL.md").write_bytes(original)
        manager = Manager(root / "library")
        skill = manager.skill("example-app", "review")
        first = skill.register(publisher, publisher_revision=1)

        # A user customizes the accepted copy; bundled publisher bytes stay intact.
        customized = original.replace(
            b"Review carefully.", b"Review carefully and check missing tests."
        )
        skill.save(
            {"SKILL.md": FileContent(customized)},
            expected_revision=first.content.revision,
        )

        # A later application bundle adds a supporting reference.
        (publisher / "references").mkdir()
        (publisher / "references" / "checks.md").write_bytes(
            b"Check failure handling.\n"
        )
        pending = skill.register(publisher, publisher_revision=2)
        assert pending.status["reconciliation_required"]
        assert pending.content.read_file("SKILL.md") == customized

        # An application shows this review. Here, assume the user approves it.
        review = skill.preview_reconciliation()
        assert review["valid"] and not review["conflicts"]
        skill.reconcile(expected_revision=review["revision"])
        accepted = skill.resolve()
        destination = root / "consumer" / accepted.name
        destination.parent.mkdir()
        receipt = accepted.export(destination)
        assert (destination / "SKILL.md").read_bytes() == customized
        assert (
            destination / "references" / "checks.md"
        ).read_bytes() == b"Check failure handling.\n"
        assert (publisher / "SKILL.md").read_bytes() == original
        print(
            json.dumps(
                {
                    "pending_review_seen": True,
                    "customized": accepted.status["customized"],
                    "exported": receipt["status"],
                    "digest": accepted.digest,
                }
            )
        )


if __name__ == "__main__":
    main()
