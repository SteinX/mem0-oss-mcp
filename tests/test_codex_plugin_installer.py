from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "plugins" / "mem0-oss" / "scripts" / "install_codex_plugin.py"


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def make_upstream_fixture(root: Path) -> Path:
    plugin_root = root / "integrations" / "mem0-plugin"
    write_json(
        plugin_root / ".codex-plugin" / "plugin.json",
        {
            "name": "mem0",
            "version": "9.9.9",
            "description": "Official fixture",
            "skills": "./skills/",
            "mcpServers": "./.codex-mcp.json",
            "hooks": "./hooks/codex-hooks.json",
            "interface": {"displayName": "Mem0"},
        },
    )
    write_json(
        plugin_root / ".mcp.json",
        {"mcpServers": {"mem0": {"url": "https://mcp.mem0.ai/mcp", "bearer_token_env_var": "MEM0_API_KEY"}}},
    )
    write_json(
        plugin_root / "hooks" / "codex-hooks.json",
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": (
                            "mcp__mem0__add_memory|mcp__plugin_mem0_mem0__add_memory|"
                            "mcp__mem0__search_memories|mcp__plugin_mem0_mem0__search_memories"
                        ),
                        "hooks": [
                            {
                                "type": "command",
                                "command": "MEM0_PLATFORM=codex ${PLUGIN_ROOT}/scripts/enforce_metadata_defaults.sh",
                            }
                        ],
                    }
                ],
                "SessionStart": [
                    {
                        "matcher": "startup|resume|compact",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "MEM0_PLATFORM=codex ${PLUGIN_ROOT}/scripts/on_session_start.sh",
                            }
                        ],
                    }
                ],
                "PostToolUse": [
                    {
                        "matcher": "mcp__mem0__.*|mcp__plugin_mem0_mem0__.*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "MEM0_PLATFORM=codex ${PLUGIN_ROOT}/scripts/on_post_tool_use.sh",
                            }
                        ],
                    }
                ],
            }
        },
    )
    scripts = plugin_root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "on_session_start.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (scripts / "enforce_metadata_defaults.sh").write_text(
        "\n".join(
            [
                '#!/usr/bin/env bash',
                'case "$TOOL_NAME" in',
                '  mcp__mem0__add_memory|mcp__plugin_mem0_mem0__add_memory) HANDLER="add_memory" ;;',
                '  mcp__mem0__search_memories|mcp__plugin_mem0_mem0__search_memories) HANDLER="search_memories" ;;',
                "esac",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (scripts / "auto_import.py").write_text("print('hosted import')\n", encoding="utf-8")
    (scripts / "auto_setup_categories.py").write_text("print('hosted categories')\n", encoding="utf-8")
    onboard = plugin_root / "skills" / "onboard"
    onboard.mkdir(parents=True, exist_ok=True)
    (onboard / "SKILL.md").write_text("---\nname: onboard\n---\n# Hosted onboard\n", encoding="utf-8")
    return root


def test_installer_generates_local_marketplace(tmp_path: Path) -> None:
    marketplace_root = tmp_path / "codex-plugins"
    result = subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--url",
            "https://mem0.example.test:18443/mcp",
            "--name",
            "mem0-example",
            "--display-name",
            "Mem0 Example",
            "--token-env-var",
            "MEM0_EXAMPLE_TOKEN",
            "--marketplace-root",
            str(marketplace_root),
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Generated plugin:" in result.stdout

    marketplace = json.loads((marketplace_root / ".agents" / "plugins" / "marketplace.json").read_text())
    assert marketplace["name"] == "mem0-oss-local"
    assert marketplace["plugins"][0]["name"] == "mem0-example"
    assert marketplace["plugins"][0]["source"]["path"] == "./plugins/mem0-example"

    plugin_root = marketplace_root / "plugins" / "mem0-example"
    manifest = json.loads((plugin_root / ".codex-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "mem0-example"
    assert manifest["interface"]["displayName"] == "Mem0 Example"
    assert manifest["version"].startswith("0.1.0+codex.")

    mcp = json.loads((plugin_root / ".mcp.json").read_text())
    server = mcp["mcpServers"]["mem0"]
    assert server["url"] == "https://mem0.example.test:18443/mcp"
    assert server["bearer_token_env_var"] == "MEM0_EXAMPLE_TOKEN"


def test_installer_uses_stdio_bridge_when_env_file_is_set(tmp_path: Path) -> None:
    marketplace_root = tmp_path / "codex-plugins"
    env_file = tmp_path / "bridge.env"
    env_file.write_text("MEM0_EXAMPLE_TOKEN=test-token\n", encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--url",
            "https://mem0.example.test:18443/mcp",
            "--name",
            "mem0-example",
            "--token-env-var",
            "MEM0_EXAMPLE_TOKEN",
            "--marketplace-root",
            str(marketplace_root),
            "--env-file",
            str(env_file),
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    plugin_root = marketplace_root / "plugins" / "mem0-example"
    assert (plugin_root / "scripts" / "mem0_oss_stdio_bridge.py").is_file()

    mcp = json.loads((plugin_root / ".mcp.json").read_text())
    server = mcp["mcpServers"]["mem0"]
    assert server["command"] == "python3"
    assert server["args"] == [str(plugin_root / "scripts" / "mem0_oss_stdio_bridge.py")]
    assert server["env"] == {
        "MEM0_OSS_MCP_URL": "https://mem0.example.test:18443/mcp",
        "MEM0_OSS_MCP_TOKEN_ENV_VAR": "MEM0_EXAMPLE_TOKEN",
        "MEM0_OSS_ENV_FILE": str(env_file),
    }
    assert "url" not in server
    assert "bearer_token_env_var" not in server


def test_installer_rejects_missing_env_file_before_generating_config(tmp_path: Path) -> None:
    marketplace_root = tmp_path / "codex-plugins"
    missing_env = tmp_path / "missing.env"

    result = subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--url",
            "https://mem0.example.test:18443/mcp",
            "--name",
            "mem0-example",
            "--marketplace-root",
            str(marketplace_root),
            "--env-file",
            str(missing_env),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "--env-file does not exist" in result.stderr
    assert not (marketplace_root / "plugins" / "mem0-example" / ".mcp.json").exists()


def test_installer_refuses_upstream_source_inside_target(tmp_path: Path) -> None:
    marketplace_root = tmp_path / "codex-plugins"
    upstream_root = make_upstream_fixture(marketplace_root / "plugins" / "mem0-example")
    manifest = upstream_root / "integrations" / "mem0-plugin" / ".codex-plugin" / "plugin.json"

    result = subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--url",
            "https://mem0.example.test:18443/mcp",
            "--name",
            "mem0-example",
            "--marketplace-root",
            str(marketplace_root),
            "--with-hooks",
            "--upstream-plugin-dir",
            str(upstream_root),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert manifest.is_file()


def test_installer_generates_full_experience_from_upstream_fixture(tmp_path: Path) -> None:
    marketplace_root = tmp_path / "codex-plugins"
    codex_dir = tmp_path / ".codex"
    env_file = tmp_path / "bridge.env"
    env_file.write_text("MEM0_EXAMPLE_TOKEN=test-token\n", encoding="utf-8")
    upstream_root = make_upstream_fixture(tmp_path / "mem0-upstream")

    cmd = [
        sys.executable,
        str(INSTALLER),
        "--url",
        "https://mem0.example.test:18443/mcp",
        "--name",
        "mem0-example",
        "--token-env-var",
        "MEM0_EXAMPLE_TOKEN",
        "--server-name",
        "mem0-team",
        "--marketplace-root",
        str(marketplace_root),
        "--with-hooks",
        "--upstream-plugin-dir",
        str(upstream_root),
        "--codex-dir",
        str(codex_dir),
        "--env-file",
        str(env_file),
    ]
    subprocess.run(cmd, text=True, capture_output=True, check=True)
    subprocess.run(cmd, text=True, capture_output=True, check=True)

    plugin_root = marketplace_root / "plugins" / "mem0-example"
    assert (plugin_root / "scripts" / "sitecustomize.py").is_file()
    assert (plugin_root / "scripts" / "mem0_oss_env.sh").is_file()
    assert "No-op replacement for hosted Mem0 Platform setup helpers" in (
        plugin_root / "scripts" / "auto_import.py"
    ).read_text()
    assert "Mem0 OSS Onboarding" in (plugin_root / "skills" / "onboard" / "SKILL.md").read_text()
    assert (plugin_root / "skills" / "mem0-oss" / "SKILL.md").is_file()

    manifest = json.loads((plugin_root / ".codex-plugin" / "plugin.json").read_text())
    assert manifest["mcpServers"] == "./.mcp.json"
    assert "hooks" not in manifest

    mcp = json.loads((plugin_root / ".codex-mcp.json").read_text())
    server = mcp["mcpServers"]["mem0-team"]
    assert server["command"] == "python3"
    assert server["env"]["MEM0_OSS_MCP_URL"] == "https://mem0.example.test:18443/mcp"
    assert server["env"]["MEM0_OSS_MCP_TOKEN_ENV_VAR"] == "MEM0_EXAMPLE_TOKEN"
    assert server["env"]["MEM0_OSS_ENV_FILE"] == str(env_file)

    hooks = json.loads((codex_dir / "hooks.json").read_text())
    entries = hooks["hooks"]["SessionStart"]
    assert len(entries) == 1
    command = entries[0]["hooks"][0]["command"]
    assert command.startswith("bash -c ")
    assert "${PLUGIN_ROOT}" not in command
    assert "MEM0_OSS_PLUGIN=mem0-example" in command
    assert "MEM0_OSS_MCP_URL=https://mem0.example.test:18443/mcp" in command
    assert "MEM0_OSS_MCP_TOKEN_ENV_VAR=MEM0_EXAMPLE_TOKEN" in command
    assert f"MEM0_OSS_ENV_FILE={env_file}" in command
    assert "mem0_oss_env.sh" in command

    pre_tool_matcher = hooks["hooks"]["PreToolUse"][0]["matcher"]
    assert pre_tool_matcher == (
        "mcp__mem0_team__add_memory|mcp__plugin_mem0_example_mem0_team__add_memory|"
        "mcp__mem0_team__search_memories|mcp__plugin_mem0_example_mem0_team__search_memories"
    )
    post_tool_matcher = hooks["hooks"]["PostToolUse"][0]["matcher"]
    assert post_tool_matcher == "mcp__mem0_team__.*|mcp__plugin_mem0_example_mem0_team__.*"

    enforce_script = (plugin_root / "scripts" / "enforce_metadata_defaults.sh").read_text()
    assert "mcp__mem0_team__add_memory" in enforce_script
    assert "mcp__plugin_mem0_example_mem0_team__add_memory" in enforce_script
    assert "mcp__plugin_mem0_mem0__" not in enforce_script

    env_result = subprocess.run(
        [
            "bash",
            "-c",
            f". {plugin_root / 'scripts' / 'mem0_oss_env.sh'}; printf '%s' \"$MEM0_API_KEY\"",
        ],
        text=True,
        capture_output=True,
        check=True,
        env={
            "MEM0_OSS_MCP_TOKEN_ENV_VAR": "MEM0_EXAMPLE_TOKEN",
            "MEM0_OSS_ENV_FILE": str(env_file),
            "MEM0_API_KEY": "cloud-token",
        },
    )
    assert env_result.stdout == "test-token"

    config = (codex_dir / "config.toml").read_text()
    assert "[features]" in config
    assert "codex_hooks = true" in config
