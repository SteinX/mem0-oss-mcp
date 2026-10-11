from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Final

import pytest

WORKFLOW: Final = Path(__file__).parents[1] / ".github/workflows/publish-ghcr.yml"


def test_release_pipeline_calls_ghcr_after_client_publication() -> None:
    release = (WORKFLOW.parent / "publish-client-plugins.yml").read_text()
    assert "  release:\n    types: [published]" in release
    image = release.split("\n  image:\n", maxsplit=1)[1]
    assert "needs: publish" in image
    assert "uses: ./.github/workflows/publish-ghcr.yml" in image
    assert "contents: read" in image
    assert "packages: write" in image
    publisher = WORKFLOW.read_text()
    assert "  workflow_call:" in publisher
    assert "  release:" not in publisher
    assert "  push:" not in publisher
    assert "  workflow_dispatch:" in publisher


@pytest.mark.parametrize(
    "event",
    [
        ("release", False, True),
        ("release", True, False),
        ("workflow_dispatch", False, False),
        ("push", False, False),
        ("pull_request", False, False),
    ],
)
def test_only_stable_releases_automatically_publish_images(
    event: tuple[str, bool, bool],
) -> None:
    release = (WORKFLOW.parent / "publish-client-plugins.yml").read_text()
    image = release.split("\n  image:\n", maxsplit=1)[1]
    condition = next(
        line.strip().removeprefix("if: ")
        for line in image.splitlines()
        if line.strip().startswith("if: ")
    )
    name, prerelease, expected = event
    result = subprocess.run(
        [
            "node",
            "-e",
            "; ".join(
                [
                    "const [event_name, prerelease] = JSON.parse(process.env.RELEASE_EVENT)",
                    "const github = {event_name, event: {release: {prerelease}}}",
                    f"process.stdout.write(String({condition}))",
                ]
            ),
        ],
        env={**os.environ, "RELEASE_EVENT": json.dumps([name, prerelease])},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(expected).lower()
