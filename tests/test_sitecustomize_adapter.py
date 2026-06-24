from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path


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


def test_sitecustomize_reads_quoted_hash_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "bridge.env"
    env_file.write_text('MEM0_EXAMPLE_TOKEN="test#token" # local bridge token\n', encoding="utf-8")

    adapter = load_adapter()

    assert adapter.read_dotenv(str(env_file)) == {"MEM0_EXAMPLE_TOKEN": "test#token"}


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
