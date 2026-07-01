from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_DIR = REPO_ROOT / "plugins" / "mem0-oss" / "scripts" / "oss_adapter"
ADAPTER = ADAPTER_DIR / "sitecustomize.py"


def load_adapter():
    original_urlopen = urllib.request.urlopen
    spec = importlib.util.spec_from_file_location("mem0_oss_sitecustomize_test", ADAPTER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        urllib.request.urlopen = original_urlopen
    return module


def test_sitecustomize_imports_and_patches_urllib() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ADAPTER_DIR)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import urllib.request; print(urllib.request.urlopen.__module__)",
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "sitecustomize"


def test_sitecustomize_reads_unquoted_hash_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "bridge.env"
    env_file.write_text("MEM0_EXAMPLE_TOKEN=test#token # local bridge token\n", encoding="utf-8")

    adapter = load_adapter()

    assert adapter.read_dotenv(str(env_file)) == {"MEM0_EXAMPLE_TOKEN": "test#token"}


def test_sitecustomize_prefers_selected_dotenv_token_before_default_env(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / "bridge.env"
    env_file.write_text("MEM0_EXAMPLE_TOKEN=selected-token\n", encoding="utf-8")
    monkeypatch.setenv("MEM0_OSS_MCP_TOKEN_ENV_VAR", "MEM0_EXAMPLE_TOKEN")
    monkeypatch.setenv("MEM0_OSS_ENV_FILE", str(env_file))
    monkeypatch.setenv("MEM0_OSS_MCP_TOKEN", "default-token")
    monkeypatch.setenv("MEM0_API_KEY", "cloud-token")

    adapter = load_adapter()

    assert adapter.resolve_token() == "selected-token"


def test_sitecustomize_does_not_cache_dotenv_token_in_environment(tmp_path: Path, monkeypatch) -> None:
    first_env = tmp_path / "first.env"
    second_env = tmp_path / "second.env"
    first_env.write_text("MEM0_EXAMPLE_TOKEN=first-token\n", encoding="utf-8")
    second_env.write_text("MEM0_EXAMPLE_TOKEN=second-token\n", encoding="utf-8")
    monkeypatch.setenv("MEM0_OSS_MCP_TOKEN_ENV_VAR", "MEM0_EXAMPLE_TOKEN")
    monkeypatch.delenv("MEM0_EXAMPLE_TOKEN", raising=False)

    adapter = load_adapter()

    monkeypatch.setenv("MEM0_OSS_ENV_FILE", str(first_env))
    assert adapter.resolve_token() == "first-token"
    assert "MEM0_EXAMPLE_TOKEN" not in os.environ

    monkeypatch.setenv("MEM0_OSS_ENV_FILE", str(second_env))
    assert adapter.resolve_token() == "second-token"
    assert "MEM0_EXAMPLE_TOKEN" not in os.environ


def test_sitecustomize_requires_mcp_url(monkeypatch) -> None:
    adapter = load_adapter()
    monkeypatch.setenv("MEM0_OSS_MCP_TOKEN", "test-token")
    monkeypatch.delenv("MEM0_OSS_MCP_URL", raising=False)

    with pytest.raises(RuntimeError, match="MEM0_OSS_MCP_URL is not set"):
        adapter.call_tool("search_memories", {})


def test_sitecustomize_preserves_urlopen_timeout(monkeypatch) -> None:
    adapter = load_adapter()
    calls = []

    def fake_dispatch(parsed, method, body, timeout=None):
        calls.append((parsed.path, method, body, timeout))
        return {"ok": True}

    monkeypatch.setattr(adapter, "dispatch_platform_call", fake_dispatch)
    request = urllib.request.Request(
        "https://api.mem0.ai/v3/memories/search",
        data=b'{"user_id":"u1"}',
    )

    response = adapter.urlopen(request, timeout=5)

    assert calls == [("/v3/memories/search", "POST", {"user_id": "u1"}, 5)]
    assert json.loads(response.read().decode("utf-8")) == {"ok": True}


def test_sitecustomize_maps_v1_event_status(monkeypatch) -> None:
    adapter = load_adapter()
    calls = []

    def fake_call_tool(name, arguments, timeout=None):
        calls.append((name, arguments, timeout))
        return {"ok": True}

    monkeypatch.setattr(adapter, "call_tool", fake_call_tool)
    parsed = urllib.parse.urlparse("https://api.mem0.ai/v1/event/evt-123/?user_id=u1")

    assert adapter.dispatch_platform_call(parsed, "GET", {}) == {"ok": True}
    assert calls == [("get_event_status", {"user_id": "u1", "event_id": "evt-123"}, None)]


def test_sitecustomize_maps_v1_events_list(monkeypatch) -> None:
    adapter = load_adapter()
    calls = []

    def fake_call_tool(name, arguments, timeout=None):
        calls.append((name, arguments, timeout))
        return {"events": []}

    monkeypatch.setattr(adapter, "call_tool", fake_call_tool)
    parsed = urllib.parse.urlparse("https://api.mem0.ai/v1/events/?page=2&page_size=10")

    assert adapter.dispatch_platform_call(parsed, "GET", {}) == {"events": []}
    assert calls == [("list_events", {"page": 2, "page_size": 10}, None)]


def test_sitecustomize_maps_v1_memory_delete(monkeypatch) -> None:
    adapter = load_adapter()
    calls = []

    def fake_call_tool(name, arguments, timeout=None):
        calls.append((name, arguments, timeout))
        return {"message": "deleted"}

    monkeypatch.setattr(adapter, "call_tool", fake_call_tool)
    parsed = urllib.parse.urlparse("https://api.mem0.ai/v1/memories/mem-123/")

    assert adapter.dispatch_platform_call(parsed, "DELETE", {}) == {"message": "deleted"}
    assert calls == [("delete_memory", {"id": "mem-123"}, None)]
