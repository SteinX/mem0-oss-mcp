from __future__ import annotations

import importlib
import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

INSTALLER = (
    Path(__file__).resolve().parents[1]
    / "plugins/mem0-oss/scripts/install_pi_plugin.py"
)


def run_installer(
    tmp_path: Path, pi_upstream: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--url",
            "https://mem0.example.test/mcp",
            "--upstream-plugin-dir",
            str(pi_upstream),
            "--target-root",
            str(tmp_path / "generated"),
            "--no-build",
            *extra,
        ],
        input="test-token-with-'quotes\n",
        capture_output=True,
        text=True,
        check=False,
    )


def test_generation_preserves_native_resources_and_keeps_api_key_private(
    tmp_path: Path, pi_upstream: Path, pi_mcp_url: str
) -> None:
    # Given an official package and token supplied through stdin.
    # When a standalone OSS copy is generated.
    result = run_installer(
        tmp_path, pi_upstream, "--api-key-stdin", "--url", pi_mcp_url
    )
    # Then native resources survive and credentials stay outside the package.
    assert result.returncode == 0, result.stderr
    plugin = tmp_path / "generated/mem0-oss"
    package = json.loads((plugin / "package.json").read_text())
    assert package["name"] == "@mem0-oss/mem0-oss-pi-plugin"
    assert "mem0ai" not in package["dependencies"]
    assert package["pi"] == {"extensions": ["./src/entry.ts"], "skills": ["./skills"]}
    assert (plugin / "skills/search/SKILL.md").is_file()
    source = (plugin / "src/entry.ts").read_text()
    assert 'from "./agent-plugin-core/lifecycle.ts"' in source
    assert source.index("initializeMem0OssEnv({") < source.index(
        "const config = loadConfig()"
    )
    assert (plugin / "src/agent-plugin-core/lifecycle.ts").is_file()
    assert (
        "...params.filters, ...resolveSearchFilters(scope, scopeCtx)"
        in (plugin / "src/memory/tools.ts").read_text()
    )
    assert (
        "filters: resolveSearchFilters(config.defaultScope, getScopeCtx())"
        in (plugin / "src/commands.ts").read_text()
    )
    assert not (plugin / "mem0_oss_memory_client.ts").exists()
    assert (plugin / "mem0_oss_pi_transport.ts").exists()
    assert package["dependencies"]["zod"] == "^4.0.0"
    env_file = tmp_path / "generated/env/mem0-oss.env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "test-token-with-" not in result.stdout
    assert all("test-token-with-" not in p.read_text() for p in plugin.rglob("*.ts"))


def test_install_preserves_settings_and_registers_package_once(
    tmp_path: Path, pi_upstream: Path
) -> None:
    # Given existing Pi settings with a configured package and model.
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    settings = agent_dir / "settings.json"
    settings.write_text(
        json.dumps({"model": "example-model", "packages": ["npm:example"]})
    )
    # When installation is repeated with source loading enabled.
    for _ in range(2):
        result = run_installer(
            tmp_path, pi_upstream, "--install", "--pi-dir", str(agent_dir)
        )
        assert result.returncode == 0, result.stderr
    # Then unrelated settings remain and the local package is registered once.
    config = json.loads(settings.read_text())
    assert config == {
        "model": "example-model",
        "packages": ["npm:example", str(tmp_path / "generated/mem0-oss")],
    }
    package = json.loads((tmp_path / "generated/mem0-oss/package.json").read_text())
    assert package["pi"]["extensions"] == ["./src/entry.ts"]


def test_incompatible_upstream_fails_before_replacing_existing_package(
    tmp_path: Path, pi_upstream: Path
) -> None:
    # Given a vendor layout with the required shared core missing.
    (
        pi_upstream / "integrations/agent-plugin-core/typescript/src/lifecycle.ts"
    ).unlink()
    marker = tmp_path / "generated/mem0-oss/keep.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("existing installation")
    # When generation is attempted.
    result = run_installer(tmp_path, pi_upstream)
    # Then the old installation survives and the missing source is explained.
    assert result.returncode == 1
    assert "shared core" in result.stderr
    assert marker.read_text() == "existing installation"


def test_build_failure_preserves_installed_package_and_settings(
    tmp_path: Path, pi_upstream: Path, pi_failing_tools: Path
) -> None:
    # Given an existing package and a build tool that fails.
    marker = tmp_path / "generated/mem0-oss/keep.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("old package")
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    settings = agent_dir / "settings.json"
    settings.write_text('{"packages":["existing"]}')
    # When the installer cannot finish the new build.
    result = subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--url",
            "https://mem0.example.test/mcp",
            "--upstream-plugin-dir",
            str(pi_upstream),
            "--target-root",
            str(tmp_path / "generated"),
            "--install",
            "--pi-dir",
            str(agent_dir),
        ],
        env={**os.environ, "PATH": str(pi_failing_tools)},
        text=True,
        capture_output=True,
        check=False,
    )
    # Then the active package and settings survive unchanged.
    assert result.returncode == 1
    if (pi_failing_tools / "node").exists():
        assert "exit status 42" in result.stderr
    else:
        assert "Node.js is required" in result.stderr
    assert marker.read_text() == "old package"
    assert settings.read_text() == '{"packages":["existing"]}'


def test_credentials_inside_package_are_rejected(
    tmp_path: Path, pi_upstream: Path
) -> None:
    # Given a credential path that would be distributed inside the package.
    env_file = tmp_path / "generated/mem0-oss/secret.env"
    # When the user selects that path.
    result = run_installer(
        tmp_path, pi_upstream, "--api-key-stdin", "--env-file", str(env_file)
    )
    # Then generation stops before writing credentials.
    assert result.returncode == 1
    assert "outside the generated package" in result.stderr
    assert not env_file.exists()


@pytest.mark.parametrize(
    "url",
    [
        "invalid",
        "https://mem0.test",
        "https://mem0.test/v1",
        "https://mem0.test/v1/",
        "https://secret@mem0.test",
        "https://mem0.test?key=secret",
        "http://example.com:notaport",
        "http://example.com:65536",
        "http://mem0 .test",
        "http://mem0%20.test",
        "http://999.0.0.1",
        "http://[v1.host]",
        "http://:80",
        "http://[::1",
        "http://mem0\\host",
        "http://mem0.\ttest",
        "http://foo％bar",
        "http://%EF%BC%8F.test",
        "http://mem0%7F.test",
        "http://a٠b.test",
    ],
)
def test_invalid_url_does_not_create_target(
    tmp_path: Path, pi_upstream: Path, url: str
) -> None:
    # Given an invalid MCP endpoint.
    # When it is supplied to the installer.
    result = run_installer(tmp_path, pi_upstream, "--url", url)
    # Then no package is generated.
    assert result.returncode == 1
    assert "--url must be" in result.stderr
    assert "secret" not in result.stdout + result.stderr
    assert not (tmp_path / "generated").exists()


def test_settings_failure_preserves_existing_package(
    tmp_path: Path, pi_upstream: Path
) -> None:
    # Given an existing package and a Pi settings location that is not a directory.
    marker = tmp_path / "generated/mem0-oss/keep.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("active package")
    agent_dir = tmp_path / "agent"
    agent_dir.write_text("keep settings location")
    # When installation cannot persist Pi settings.
    result = run_installer(
        tmp_path, pi_upstream, "--install", "--pi-dir", str(agent_dir)
    )
    # Then the original active package and settings location remain intact.
    assert result.returncode == 1
    assert marker.read_text() == "active package"
    assert agent_dir.read_text() == "keep settings location"


def test_settings_commit_failure_rolls_back_package_and_key(
    tmp_path: Path, pi_upstream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given active package, settings, and private credentials.
    marker = tmp_path / "generated/mem0-oss/keep.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("active package")
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    settings = agent_dir / "settings.json"
    settings.write_text('{"packages":["existing"]}')
    env_file = tmp_path / "key.env"
    env_file.write_text("MEM0_OSS_MCP_TOKEN=old-fixture-key\n")
    env_file.chmod(0o600)
    original_replace = Path.replace

    def fail_settings_replace(path: Path, target: str | os.PathLike[str]) -> Path:
        if Path(target) == settings:
            raise OSError("fixture settings commit failure")
        return original_replace(path, target)

    monkeypatch.syspath_prepend(str(INSTALLER.parent))
    installer = importlib.import_module("install_pi_plugin")
    monkeypatch.setattr(Path, "replace", fail_settings_replace)
    monkeypatch.setattr(sys, "stdin", io.StringIO("new-fixture-key"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(INSTALLER),
            "--url",
            "https://mem0.test/mcp",
            "--upstream-plugin-dir",
            str(pi_upstream),
            "--target-root",
            str(tmp_path / "generated"),
            "--no-build",
            "--install",
            "--pi-dir",
            str(agent_dir),
            "--api-key-stdin",
            "--env-file",
            str(env_file),
        ],
    )
    # When the final settings replacement fails after package promotion.
    with pytest.raises(OSError, match="fixture settings commit failure"):
        installer.main()
    # Then all active state is restored and failed staging/backups are removed.
    assert marker.read_text() == "active package"
    assert settings.read_text() == '{"packages":["existing"]}'
    assert env_file.read_text() == "MEM0_OSS_MCP_TOKEN=old-fixture-key\n"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert not list((tmp_path / "generated").glob("mem0-oss.backup.*"))
