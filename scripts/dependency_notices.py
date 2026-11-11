"""Version-bound upstream notices missing from otherwise unchanged wheels."""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def supplemental_notices(name, version, wheel, output):
    if (name.lower().replace("_", "-"), version) != ("github-copilot-sdk", "1.0.26"):
        return []
    expected_wheel = "0854a5a34e3d89a8392ddafff5de78df2071937354f97c4865536b3c5ba15abe"
    expected_notice = "2510b446bc1f0cf9702453075d20cd88631e20e5642658edb7325d9c1eb534f7"
    with wheel.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected_wheel:
            raise RuntimeError(
                "Copilot SDK notice supplement requires the reviewed wheel"
            )
    content = (
        ROOT
        / "conversation-manager/src/conversation_manager/notices/github-copilot-sdk-1.0.26.LICENSE"
    ).read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_notice:
        raise RuntimeError("Copilot SDK upstream license changed")
    logical = "share/notices/github-copilot-sdk/1.0.26/LICENSE"
    destination = output / logical
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return [
        dict(
            path=logical,
            sha256=expected_notice,
            source="https://github.com/github/copilot-sdk/blob/v1.0.26/LICENSE",
        )
    ]
