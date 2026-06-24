from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE = REPO_ROOT / "plugins" / "mem0-oss" / "scripts" / "oss_adapter" / "mem0_oss_stdio_bridge.py"


class ProxyHandler(BaseHTTPRequestHandler):
    request_body = ""
    authorization = ""

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        type(self).request_body = self.rfile.read(length).decode("utf-8")
        type(self).authorization = self.headers.get("authorization", "")
        request = json.loads(type(self).request_body)
        response = {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"ok": True},
        }
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class ConcurrentProxyHandler(BaseHTTPRequestHandler):
    second_request_seen = threading.Event()
    first_request_unblocked = False

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        request = json.loads(self.rfile.read(length).decode("utf-8"))
        if request["id"] == 2:
            type(self).second_request_seen.set()
        elif request["id"] == 1:
            type(self).first_request_unblocked = type(self).second_request_seen.wait(timeout=5)

        response = {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"ok": True},
        }
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def test_stdio_bridge_reads_env_file_and_proxies_json_rpc(tmp_path: Path) -> None:
    env_file = tmp_path / "bridge.env"
    env_file.write_text('MEM0_EXAMPLE_TOKEN="test#token" # local bridge token\n', encoding="utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    try:
        env = os.environ.copy()
        env.update(
            {
                "MEM0_OSS_MCP_URL": f"http://127.0.0.1:{server.server_port}/mcp",
                "MEM0_OSS_MCP_TOKEN_ENV_VAR": "MEM0_EXAMPLE_TOKEN",
                "MEM0_OSS_ENV_FILE": str(env_file),
                "MEM0_API_KEY": "cloud-token",
            }
        )
        result = subprocess.run(
            [sys.executable, str(BRIDGE)],
            input='{"jsonrpc":"2.0","id":7,"method":"tools/list","params":{}}\n',
            text=True,
            capture_output=True,
            env=env,
            check=True,
            timeout=10,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert json.loads(ProxyHandler.request_body)["method"] == "tools/list"
    assert ProxyHandler.authorization == "Bearer test#token"
    assert json.loads(result.stdout) == {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {"ok": True},
    }


def test_stdio_bridge_proxies_requests_concurrently(tmp_path: Path) -> None:
    env_file = tmp_path / "bridge.env"
    env_file.write_text("MEM0_EXAMPLE_TOKEN=test-token\n", encoding="utf-8")
    ConcurrentProxyHandler.second_request_seen.clear()
    ConcurrentProxyHandler.first_request_unblocked = False

    server = ThreadingHTTPServer(("127.0.0.1", 0), ConcurrentProxyHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    try:
        env = os.environ.copy()
        env.update(
            {
                "MEM0_OSS_MCP_URL": f"http://127.0.0.1:{server.server_port}/mcp",
                "MEM0_OSS_MCP_TOKEN_ENV_VAR": "MEM0_EXAMPLE_TOKEN",
                "MEM0_OSS_ENV_FILE": str(env_file),
            }
        )
        result = subprocess.run(
            [sys.executable, str(BRIDGE)],
            input=(
                '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{}}\n'
                '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n'
            ),
            text=True,
            capture_output=True,
            env=env,
            check=True,
            timeout=10,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    responses = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert {response["id"] for response in responses} == {1, 2}
    assert ConcurrentProxyHandler.first_request_unblocked
