from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from test_codex_plugin_installer import INSTALLER, write_json


def test_native_codex_hooks_run_with_private_oss_configuration(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream" / "integrations" / "codex-plugin"
    write_json(upstream / ".codex-plugin" / "plugin.json", {
        "name": "mem0", "version": "0.3.3", "skills": "./skills/",
        "hooks": "./hooks/hooks.json", "mcpServers": "./.mcp.json",
    })
    write_json(upstream / "hooks" / "hooks.json", {"hooks": {"SessionStart": [{
        "hooks": [{"type": "command", "command":
            'python3 "${PLUGIN_ROOT}/core/probe.py" --data "${PLUGIN_DATA}"'}],
    }]}})
    core = upstream / "core"
    core.mkdir()
    (core / "memory_core.py").write_text(
        'from __future__ import annotations\n'
        'def send(packet_id, index, body, messages):\n'
        '    return capture(\n                {**body, "messages": messages},\n                15,\n    )\n'
        'def _result_count(response: dict[str, Any] | list[Any]) -> int:\n'
        '    if isinstance(response, dict):\n'
        '        return len(response.get("results", []))\n'
        '    return len(response)\n',
        encoding="utf-8",
    )
    (core / "probe.py").write_text(
        "import memory_core\nimport json,os,sys,urllib.request\n"
        "memory_core.capture = lambda body, timeout: {'body': body, 'timeout': timeout}\n"
        "batch = memory_core.send('packet-one', 0, {}, [])\n"
        "again = memory_core.send('packet-one', 0, {}, [])\n"
        "other = memory_core.send('packet-one', 1, {}, [])\n"
        "count = memory_core._result_count({'result_count': 5, 'results': [{}]})\n"
        "print(json.dumps({'count': count, 'batch': batch, 'stable': batch == again, 'distinct': batch != other, "
        "'url': os.environ.get('MEM0_OSS_MCP_URL'), "
        "'key_loaded': os.environ.get('MEM0_API_KEY') == 'fixture-client-key', "
        "'scope': os.environ.get('MEM0_CODE_SEARCH_SCOPE'), "
        "'telemetry': os.environ.get('MEM0_TELEMETRY'), "
        "'data_dir': os.environ.get('MEM0_CODE_DATA_DIR'), "
        "'adapter': urllib.request.urlopen.__module__, 'args': sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    env_file = tmp_path / "credential.env"
    env_file.write_text("MEM0_OSS_MCP_TOKEN=fixture-client-key\n", encoding="utf-8")
    marketplace = tmp_path / "marketplace with spaces"
    codex_dir = tmp_path / "codex"

    subprocess.run([
        sys.executable, str(INSTALLER), "--url", "https://oss.example.test/mcp",
        "--name", "mem0", "--with-hooks", "--upstream-plugin-dir", str(upstream),
        "--env-file", str(env_file), "--marketplace-root", str(marketplace),
        "--codex-dir", str(codex_dir), "--no-enable-codex-hooks",
    ], check=True, capture_output=True, text=True)
    plugin = marketplace / "plugins" / "mem0"
    hook_config = json.loads((codex_dir / "hooks.json").read_text())
    command = hook_config["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    hook = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
    result = json.loads(hook.stdout)

    assert result["url"] == "https://oss.example.test/mcp"
    assert result["key_loaded"] is True
    assert result["scope"] == "mine"
    assert result["telemetry"] == "false"
    assert result["adapter"] == "mem0_oss_adapter"
    assert result["stable"] is True
    assert result["count"] == 5
    assert result["distinct"] is True
    assert result["batch"]["timeout"] == 90
    assert result["batch"]["body"]["idempotency_key"].startswith("codex-")
    assert result["args"] == ["--data", str(codex_dir / "mem0-oss-data" / "mem0")]
    assert "hooks" not in json.loads((plugin / ".codex-plugin" / "plugin.json").read_text())

    standalone = subprocess.run(
        [sys.executable, str(plugin / "core" / "probe.py")],
        check=True, capture_output=True, text=True,
    )
    assert json.loads(standalone.stdout)["key_loaded"] is True
    assert json.loads(standalone.stdout)["adapter"] == "mem0_oss_adapter"
    assert json.loads(standalone.stdout)["data_dir"] == str(codex_dir / "mem0-oss-data" / "mem0")
