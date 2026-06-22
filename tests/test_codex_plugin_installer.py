from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "plugins" / "mem0-oss" / "scripts" / "install_codex_plugin.py"


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
