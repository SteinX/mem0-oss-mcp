from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

INSTALLER = (
    Path(__file__).resolve().parents[1]
    / "plugins/mem0-oss/scripts/install_pi_plugin.py"
)
sys.path.insert(0, str(INSTALLER.parent))
from package_pi_release import package_release
from pi_plugin_layout import InstallerError


def test_portable_generation_uses_runtime_connection_and_host_directory(
    tmp_path: Path, pi_upstream: Path
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--portable",
            "--upstream-plugin-dir",
            str(pi_upstream),
            "--target-root",
            str(tmp_path / "generated"),
            "--no-build",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    plugin = tmp_path / "generated/mem0-oss"
    entry = (plugin / "src/entry.ts").read_text()
    config = (plugin / "src/config/index.ts").read_text()
    assert 'url: process.env.MEM0_OSS_MCP_URL || ""' in entry
    assert "envFile: undefined" in entry
    assert "getAgentDir()" in config
    assert str(tmp_path) not in config
    assert "autoCapture: false" in config
    assert json.loads((plugin / "package.json").read_text())["pi"]["extensions"] == [
        "./src/entry.ts"
    ]


@pytest.mark.parametrize("option", ["--install", "--token-stdin", "--env-file"])
def test_portable_generation_rejects_machine_configuration(
    tmp_path: Path, pi_upstream: Path, option: str
) -> None:
    arguments = (
        [option, str(tmp_path / "private.env")] if option == "--env-file" else [option]
    )
    result = subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--portable",
            "--upstream-plugin-dir",
            str(pi_upstream),
            *arguments,
        ],
        input="private-test-token",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "portable packages cannot" in result.stderr
    assert "private-test-token" not in result.stdout + result.stderr


def test_archive_allowlist_excludes_private_files_and_build_paths(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "built"
    (plugin / "dist").mkdir(parents=True)
    (plugin / "dist/entry.js").write_text("export default function() {}")
    (plugin / "dist/entry.js.map").write_text("/private/build-machine/path")
    (plugin / "secret.env").write_text("PRIVATE_TOKEN=never-publish")
    (plugin / "package.json").write_text(
        json.dumps(
            {"version": "0.3.2+oss.stamp", "dependencies": {"zod": "4"}, "pi": {}}
        )
    )
    archives = package_release(plugin, tmp_path / "output", "0.1.6", "a" * 40)
    with zipfile.ZipFile(archives[0]) as archive:
        names = archive.namelist()
        assert "mem0-oss/dist/entry.js" in names
        assert all("secret" not in name and not name.endswith(".map") for name in names)
        manifest = json.loads(archive.read("mem0-oss/package.json"))
        assert manifest["dependencies"] == {}
        assert manifest["pi"]["extensions"] == ["./dist/entry.js"]
        assert b"never-publish" not in b"".join(archive.read(name) for name in names)
    repeated = package_release(plugin, tmp_path / "second", "0.1.6", "a" * 40)
    assert [path.read_bytes() for path in repeated] == [
        path.read_bytes() for path in archives
    ]


def test_archive_rejects_symlink_resource(tmp_path: Path) -> None:
    plugin = tmp_path / "built"
    (plugin / "dist").mkdir(parents=True)
    (tmp_path / "outside.js").write_text("private content")
    (plugin / "dist/entry.js").symlink_to(tmp_path / "outside.js")
    (plugin / "package.json").write_text('{"version":"0.3.2","pi":{}}')
    with pytest.raises(InstallerError, match="regular files"):
        package_release(plugin, tmp_path / "output", "0.1.6", "a" * 40)
