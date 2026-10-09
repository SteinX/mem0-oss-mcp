import os
import subprocess
from pathlib import Path

import pytest
from test_release_source import WORKFLOW, workflow_script


@pytest.mark.parametrize(
    ("release_tag", "push_latest"),
    [("0.1.6", True), ("0.1.6-rc.1", False), ("0.1.5", False), ("", True), ("", False)],
)
def test_publisher_pushes_immutable_tags_before_promotion(
    tmp_path: Path, release_tag: str, push_latest: bool
) -> None:
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$DOCKER_CALLS"\n')
    docker.chmod(0o755)
    calls = tmp_path / "docker-calls.txt"
    source_sha = "a" * 40
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", workflow_script("build")],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "DOCKER_CALLS": str(calls),
            "REGISTRY": "ghcr.io",
            "IMAGE_NAME": "SteinX/mem0-oss-mcp",
            "SOURCE_SHA": source_sha,
            "RELEASE_TAG": release_tag,
            "PUSH_LATEST": str(push_latest).lower(),
            "GITHUB_REPOSITORY": "SteinX/mem0-oss-mcp",
            "GITHUB_REF": "refs/tags/" + release_tag
            if release_tag
            else "refs/heads/main",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    image = "ghcr.io/steinx/mem0-oss-mcp"
    expected = [f"push {image}:{source_sha}"]
    if release_tag:
        expected.append(f"push {image}:{release_tag}")
    actual = calls.read_text().splitlines()
    assert [call for call in actual if call.startswith("push ")] == expected
    assert f"--label org.opencontainers.image.revision={source_sha}" in actual[0]
    assert (
        f"--label org.opencontainers.image.version={release_tag or source_sha}"
        in actual[0]
    )


def test_release_and_manual_tag_source_settings() -> None:
    workflow = WORKFLOW.read_text()
    assert "types: [published]" in workflow
    assert "github.ref_type == 'tag' && github.ref_name" in workflow
    assert (
        "github.event_name == 'release' && !github.event.release.prerelease" in workflow
    )
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "github.ref_type == 'tag' && inputs.push_latest" in workflow
    assert "group: mcp-image-publication" in workflow
    assert "queue: max" in workflow


@pytest.mark.parametrize(
    ("event", "requested", "current_tag", "expected"),
    [
        ("release", True, "0.1.6", True),
        ("release", True, "0.1.7", False),
        ("release", False, "0.1.7", False),
        ("workflow_dispatch", True, "0.1.7", True),
    ],
)
def test_latest_promotion_checks_the_current_release(
    tmp_path: Path, event: str, requested: bool, current_tag: str, expected: bool
) -> None:
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/bash\nprintf "%s\\n" "$CURRENT_TAG"\n')
    gh.chmod(0o755)
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", workflow_script("latest")],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GITHUB_EVENT_NAME": event,
            "GITHUB_REPOSITORY": "SteinX/mem0-oss-mcp",
            "PUSH_LATEST": str(requested).lower(),
            "RELEASE_TAG": "0.1.6",
            "CURRENT_TAG": current_tag,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text().strip() == f"push_latest={str(expected).lower()}"


def test_latest_promotion_after_immutable_builds() -> None:
    workflow = WORKFLOW.read_text()
    assert (
        workflow.index("- name: Build and push image")
        < workflow.index("- name: Resolve latest promotion")
        < workflow.index("- name: Promote latest")
    )
    assert "if: steps.latest.outputs.push_latest == 'true'" in workflow


def test_latest_promotion_uses_the_built_source(tmp_path: Path) -> None:
    image = "ghcr.io/steinx/mem0-oss-mcp"
    sha = "a" * 40
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_CALLS"\n')
    docker.chmod(0o755)
    calls = tmp_path / "docker-calls.txt"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", workflow_script("promote")],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "DOCKER_CALLS": str(calls),
            "REGISTRY": "ghcr.io",
            "IMAGE_NAME": "SteinX/mem0-oss-mcp",
            "SOURCE_SHA": sha,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert calls.read_text().splitlines() == [
        f"tag {image}:{sha} {image}:latest",
        f"push {image}:latest",
    ]
