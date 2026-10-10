from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/mem0-oss/scripts"
sys.path.insert(0, str(SCRIPTS))
import install_client_release as installer
import release_host
from build_client_releases import package_client
from release_archives import write_archives
from release_config import ReleaseError, load_codex_environment, read_connection
from release_state import extract_verified, snapshots


def test_native_registration_creates_fresh_host_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = tmp_path / "fresh-codex"

    def run(*args: str, **kwargs: str) -> None:
        assert host.is_dir()

    monkeypatch.setattr(release_host.subprocess, "run", run)
    release_host.register(
        "codex", tmp_path / "root", host, tmp_path / "connection.json"
    )


def archive_fixture(tmp_path: Path, client: str, version: str) -> Path:
    public = {
        "provenance.json": json.dumps(
            {"client": client, "bridge_version": version}
        ).encode(),
        ".mcp.json": b'{"mcpServers":{"mem0":{"command":"python3","args":[]}}}',
        "hooks/hooks.json": b'{"hooks":{}}',
        "skills/status/SKILL.md": b"status",
        "dist/index.js": b"export default async () => ({});",
    }
    return write_archives(public, tmp_path, f"mem0-oss-{client}-{version}")[0]


def arguments(tmp_path: Path, client: str, archive: Path) -> argparse.Namespace:
    return argparse.Namespace(
        root=tmp_path / "root",
        client=client,
        host_dir=tmp_path / "host",
        action="install",
        archive=archive,
        version="latest",
        url="https://fixture.example/mcp",
        env_file=None,
        token_env_var=None,
        token_stdin=False,
    )


@pytest.mark.parametrize("client", ["codex", "opencode"])
def test_install_upgrade_rollback_preserves_external_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: str
) -> None:
    monkeypatch.setattr(installer, "register", lambda *args: None)
    args = arguments(
        tmp_path, client, archive_fixture(tmp_path / "v1", client, "1.0.0")
    )
    env = tmp_path / "external.env"
    env.write_text("MEM0_OSS_MCP_TOKEN=private-fixture-token\n")
    args.env_file = env
    installer.install(args)
    root = args.root / "clients" / client
    first = (root / "current").resolve()
    if client == "codex":
        mcp = json.loads((first / ".mcp.json").read_text())
        assert mcp["mcpServers"]["mem0"]["args"][0] == str(
            first / "scripts/release_mcp.py"
        )
    configuration = (root / "connection.json").read_bytes()
    assert (root / "connection.json").stat().st_mode & 0o777 == 0o600
    assert b"private-fixture-token" not in b"".join(
        path.read_bytes() for path in first.rglob("*") if path.is_file()
    )
    args.action = "update"
    args.url = args.env_file = None
    args.archive = archive_fixture(tmp_path / "v2", client, "1.1.0")
    installer.install(args)
    assert (root / "current").resolve() != first
    assert (root / "connection.json").read_bytes() == configuration
    args.action = "rollback"
    installer.install(args)
    assert (root / "current").resolve() == first
    assert (root / "connection.json").read_bytes() == configuration
    assert env.read_text() == "MEM0_OSS_MCP_TOKEN=private-fixture-token\n"


def test_registration_failure_restores_selection_credentials_and_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(installer, "register", lambda *args: None)
    args = arguments(
        tmp_path, "codex", archive_fixture(tmp_path / "v1", "codex", "1.0.0")
    )
    installer.install(args)
    root = args.root / "clients/codex"
    selected = (root / "current").resolve()
    config = (root / "connection.json").read_bytes()
    host = args.host_dir / "config.toml"
    host.parent.mkdir(exist_ok=True)
    host.write_text('model = "keep-me"\n')

    def fail(*args: Path) -> None:
        host.write_text("broken registration")
        raise subprocess.CalledProcessError(1, "codex")

    monkeypatch.setattr(installer, "register", fail)
    args.archive = archive_fixture(tmp_path / "v2", "codex", "1.1.0")
    args.url = "https://changed.example/mcp"
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(args)
    assert (root / "current").resolve() == selected
    assert (root / "connection.json").read_bytes() == config
    assert host.read_text() == 'model = "keep-me"\n'
    assert not (root / "transaction.json").exists()


def test_interrupted_switch_recovers_before_next_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(installer, "register", lambda *args: None)
    args = arguments(
        tmp_path, "opencode", archive_fixture(tmp_path / "v1", "opencode", "1.0.0")
    )
    installer.install(args)
    root = args.root / "clients/opencode"
    selected = str((root / "current").resolve())
    config = root / "connection.json"
    content = config.read_bytes()
    (root / "transaction.json").write_text(
        json.dumps({"current": selected, "files": snapshots([config])})
    )
    config.write_text("interrupted write")
    args.action = "rollback"
    with pytest.raises(ReleaseError, match="no previous release"):
        installer.install(args)
    assert config.read_bytes() == content
    assert str((root / "current").resolve()) == selected
    assert not (root / "transaction.json").exists()


@pytest.mark.parametrize(
    "name",
    ["mem0-oss/../../outside", "/tmp/outside", "other/file", "mem0-oss/evil\\name"],
)
def test_archive_rejects_unsafe_member_paths(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(name, "bad")
    archive.with_suffix(".sha256").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    )
    with pytest.raises(ReleaseError, match="unsafe"):
        extract_verified(archive, tmp_path / "out", "codex")


def test_wrong_checksum_rejected_before_extracting(tmp_path: Path) -> None:
    archive = archive_fixture(tmp_path, "codex", "1.0.0")
    archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(ReleaseError, match="checksum"):
        extract_verified(archive, tmp_path / "out", "codex")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.com/mcp",
        "https://example.com/mcp?secret=a",
        "https://example.com/mcp#fragment",
        "file:///mcp",
    ],
)
def test_connection_rejects_credential_urls_without_echoing_values(
    tmp_path: Path, url: str
) -> None:
    config = tmp_path / "connection.json"
    config.write_text(json.dumps({"url": url}))
    with pytest.raises(ReleaseError) as error:
        read_connection(config)
    assert url not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("url", "https://example.com\\evil/mcp"),
        ("url", "https://example.com/\nmcp"),
        ("user_id", ""),
        ("token_env_var", "bad-name"),
    ],
)
def test_connection_rejects_invalid_strings(
    tmp_path: Path, field: str, value: str
) -> None:
    path = tmp_path / "connection.json"
    path.write_text(json.dumps({"url": "https://original.example/mcp", field: value}))
    with pytest.raises(ReleaseError):
        read_connection(path)


def test_relative_env_path_is_resolved_against_connection_file(tmp_path: Path) -> None:
    path = tmp_path / "connection.json"
    path.write_text(
        '{"url":"https://original.example/mcp","env_file":"credentials.env"}'
    )
    assert read_connection(path)["env_file"] == str(tmp_path / "credentials.env")


def test_codex_origin_override_cannot_reuse_file_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "environ", dict(os.environ))
    path = tmp_path / "connection.json"
    path.write_text(
        '{"url":"https://original.example/mcp","env_file":"credentials.env"}'
    )
    monkeypatch.setenv("MEM0_OSS_CLIENT_CONFIG", str(path))
    monkeypatch.setenv("MEM0_OSS_MCP_URL", "https://other.example/mcp")
    for name in ("MEM0_OSS_MCP_TOKEN", "MEM0_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ReleaseError, match="origin"):
        load_codex_environment()
    monkeypatch.setenv("MEM0_OSS_MCP_TOKEN", "explicit-token-for-other-origin")
    load_codex_environment()


@pytest.mark.parametrize("client", ["codex", "opencode"])
def test_release_allowlist_excludes_private_files(tmp_path: Path, client: str) -> None:
    plugin = tmp_path / "plugin"
    for name, content in {
        ".codex-plugin/plugin.json": '{"version":"1.0.0"}',
        ".mcp.json": '{"mcpServers":{}}',
        "hooks/hooks.json": '{"hooks":{}}',
        "core/runtime.py": "pass",
        "scripts/runtime.py": "pass",
        "skills/status/SKILL.md": "status",
        "dist/index.js": "export default async () => ({});",
        "opencode-skills/status/SKILL.md": "status",
        "credentials.env": "PRIVATE_KEY=archive-must-exclude-this",
        "connection.json": '{"url":"https://private-server.example/mcp"}',
        "dist/index.js.map": "/private/build/path",
    }.items():
        path = plugin / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    license_path = tmp_path / "LICENSE"
    license_path.write_text("fixture-license")
    archives = package_client(
        plugin, client, tmp_path / "assets", "1.0.0", "a" * 40, license_path
    )
    with zipfile.ZipFile(archives[0]) as archive:
        names = archive.namelist()
        content = b"".join(archive.read(name) for name in names)
        assert "mem0-oss/install.py" in names
        assert "mem0-oss/release_json.py" in names
        assert "mem0-oss/OSS_LICENSE" in names
        assert not any(name.endswith((".env", ".map")) for name in names)
        assert b"archive-must-exclude-this" not in content
        assert b"private-server.example" not in content
