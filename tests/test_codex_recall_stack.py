from __future__ import annotations

import importlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not (os.environ.get("MEM0_COMPAT_CORE_ROOT") and os.environ.get("MEM0_COMPAT_SIDECAR_ROOT")),
    reason="cross-repository test requires Core and Sidecar checkout paths",
)


@contextmanager
def http_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def gateway(client):
    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            response = client.post(self.path, content=body, headers={"Content-Type": "application/json"})
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, format, *args):
            return

    return Gateway


def test_generated_codex_plugin_recalls_through_bridge_sidecar_and_core(tmp_path, monkeypatch):
    core_root = Path(os.environ["MEM0_COMPAT_CORE_ROOT"])
    sidecar_root = Path(os.environ["MEM0_COMPAT_SIDECAR_ROOT"])
    monkeypatch.syspath_prepend(str(ROOT / "src"))
    monkeypatch.syspath_prepend(str(core_root))
    monkeypatch.syspath_prepend(str(core_root / "server"))
    monkeypatch.syspath_prepend(str(sidecar_root / "src"))
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("MEM0_TELEMETRY", "false")
    monkeypatch.setenv("MEM0_DIR", str(tmp_path / "sdk-data"))

    from fastapi.testclient import TestClient
    from mem0 import Memory
    from mem0_sidecar.config import SidecarSettings
    from mem0_sidecar.http_adapter.app import create_app
    from mem0_sidecar.mem0_client.client import Mem0RestClient
    from mem0_sidecar.store.repositories import MemoryIndexRepository

    from mem0_oss_mcp import server as bridge

    memory = Memory.__new__(Memory)
    memory.api_version = "v1.1"
    memory.embedding_model = Mock()
    memory.embedding_model.embed.return_value = [0.1, 0.2]
    memory.vector_store = Mock()
    memory.vector_store.keyword_search.return_value = []
    memory.vector_store.search.return_value = [
        SimpleNamespace(id="old", score=0.99, payload={"data": "Old state", "superseded_by": "current"}),
        SimpleNamespace(id="current", score=0.8, payload={
            "data": "Verified current state", "state": "current",
            "created_at": "2026-10-18T10:00:00Z", "updated_at": "2026-10-18T11:00:00Z",
        }),
        SimpleNamespace(id="unclassified", score=0.7, payload={"data": "Independent fact"}),
    ]
    memory.reranker = Mock()
    memory.reranker.rerank.side_effect = lambda query, memories, limit: list(reversed(memories))[:limit]
    record_factory = logging.getLogRecordFactory()
    try:
        with patch("mem0.Memory.from_config", return_value=memory), patch("server_state._load_overrides", return_value={}):
            backend = importlib.import_module("server.main")
    finally:
        logging.setLogRecordFactory(record_factory)
    monkeypatch.setattr(backend, "_should_log_request", lambda request: False)
    monkeypatch.setattr("mem0.memory.main.extract_entities", lambda query: [])

    with http_server(gateway(TestClient(backend.app))) as core_url:
        sidecar = create_app(
            settings=SidecarSettings(
                database_url=f"sqlite:///{tmp_path / 'sidecar.sqlite3'}",
                mem0_base_url=core_url,
                default_project_id="recall-project",
                client_auth_enabled=False,
            ),
            mem0_client=Mem0RestClient(base_url=core_url),
        )
        with sidecar.state.session_factory() as session:
            for memory_id in ("old", "current", "unclassified"):
                MemoryIndexRepository(session).upsert_memory(
                    project_id="recall-project", mem0_memory_id=memory_id,
                    user_id="recall-user", app_id="recall-app", category=None, metadata={},
                )
            session.commit()
        with http_server(gateway(TestClient(sidecar))) as sidecar_url:
            monkeypatch.setattr(bridge.Config, "sidecar_base_url", sidecar_url)
            monkeypatch.setattr(bridge.Config, "sidecar_project_id", "recall-project")
            monkeypatch.setattr(bridge.Config, "sidecar_api_key", "")
            monkeypatch.setattr(bridge.Config, "token", "fixture-bridge-token")
            monkeypatch.setattr(bridge.Config, "authenticator", bridge.McpAuthenticator.from_env("", {
                "MEM0_OSS_MCP_AUTH_MODE": "static", "MEM0_OSS_MCP_TOKEN": "fixture-bridge-token",
            }))
            with http_server(bridge.Handler) as bridge_url:
                subprocess.run(
                    [
                        sys.executable, str(ROOT / "plugins/mem0-oss/scripts/install_codex_plugin.py"),
                        "--url", f"{bridge_url}/mcp", "--token-stdin", "--name", "mem0",
                        "--with-hooks", "--upstream-plugin-dir", str(core_root),
                        "--marketplace-root", str(tmp_path / "marketplace"),
                        "--codex-dir", str(tmp_path / "codex"), "--no-enable-codex-hooks",
                    ],
                    input="fixture-bridge-token\n", text=True, capture_output=True, check=True,
                )
                plugin = tmp_path / "marketplace/plugins/mem0"
                environment = {
                    **os.environ, "MEM0_CODE_USER_ID": "recall-user", "MEM0_PROJECT_ID": "recall-app",
                    "MEM0_CODE_DATA_DIR": str(tmp_path / "hook-data"), "MEM0_CODE_AUTO_FLUSH": "false",
                }

                def prompt(text: str) -> str:
                    result = subprocess.run(
                        [sys.executable, str(plugin / "hooks/adapter.py"), "user-prompt"],
                        input=json.dumps({"session_id": "stack-session", "cwd": str(tmp_path), "prompt": text}),
                        env=environment, text=True, capture_output=True, timeout=3, check=True,
                    )
                    return result.stdout

                assert prompt("继续") == ""
                first_full = prompt("Where is the verified current project state recorded?")
                with sqlite3.connect(tmp_path / "hook-data/evidence.sqlite3") as evidence:
                    errors = evidence.execute("SELECT error FROM operations WHERE success = 0").fetchall()
                assert first_full, errors
                context = json.loads(first_full)
                injected = context["hookSpecificOutput"]["additionalContext"]
                assert "id=current" in injected and "id=unclassified" in injected
                assert "id=old" not in injected
                assert "2026-10-18T11:00:00Z" in injected
                assert prompt("Where is the verified current project state recorded?") == ""
                assert memory.vector_store.search.call_count == 1
                memory.reranker.rerank.assert_not_called()

                def search(latest_only: bool, rerank: bool):
                    request = Request(
                        f"{bridge_url}/mcp",
                        data=json.dumps({
                            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "search_memories", "arguments": {
                                "query": "state", "user_id": "recall-user", "app_id": "recall-app",
                                "top_k": 3, "latest_only": latest_only, "rerank": rerank,
                            }},
                        }).encode(),
                        headers={"Content-Type": "application/json", "Authorization": "Bearer fixture-bridge-token"},
                    )
                    with urlopen(request, timeout=3) as response:
                        envelope = json.load(response)
                    assert not envelope["result"].get("isError"), envelope
                    return json.loads(envelope["result"]["content"][0]["text"])["results"]

                assert [item["id"] for item in search(True, True)] == ["unclassified", "current"]
                memory.reranker.rerank.assert_called_once()
                assert [item["id"] for item in search(False, False)] == ["old", "current", "unclassified"]
