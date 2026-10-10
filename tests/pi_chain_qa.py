import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from http.client import HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.error import URLError
from urllib.request import urlopen

from mem0_sidecar.store.models import MemoryIndex
from pydantic import JsonValue
from sqlalchemy import create_engine, insert


class CoreFixture(BaseHTTPRequestHandler):
    protocol_version: str = "HTTP/1.1"
    rows: ClassVar[dict[str, dict[str, JsonValue]]] = {}
    reads: ClassVar[int] = 0

    def log_message(self, format: str, *args: JsonValue) -> None:
        return

    def respond(self, status: int, body: JsonValue) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        _ = self.wfile.write(raw)

    def authenticated(self) -> bool:
        if self.headers.get("X-API-Key") == "core-fixture-token":
            return True
        self.respond(401, {"detail": "unauthorized"})
        return False

    def do_GET(self) -> None:
        if not self.authenticated():
            return
        if self.path == "/auth/me":
            self.respond(
                200,
                {
                    "id": "fixture-admin",
                    "role": "admin",
                    "credential": {
                        "kind": "core_api_key",
                        "id": "fixture-key",
                        "label": "fixture",
                        "key_prefix": "fixture",
                    },
                },
            )
            return
        if self.path == "/qa/stats":
            self.respond(200, {"reads": CoreFixture.reads, "remaining": len(self.rows)})
            return
        if self.path.startswith("/memories/"):
            CoreFixture.reads += 1
            row = self.rows.get(self.path.rsplit("/", 1)[-1])
            self.respond(200 if row else 404, row or {"detail": "not found"})
            return
        self.respond(404, {"detail": "not found"})

    def do_DELETE(self) -> None:
        if not self.authenticated():
            return
        memory_id = self.path.rsplit("/", 1)[-1]
        removed = self.rows.pop(memory_id, None)
        self.respond(
            200 if removed else 404, {"id": memory_id, "deleted": removed is not None}
        )


class FixtureServer(ThreadingHTTPServer):
    request_queue_size: int = 64


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def ready(url: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                assert isinstance(response, HTTPResponse)
                assert response.status == 200
                return
        except (URLError, TimeoutError):
            time.sleep(0.1)
    raise AssertionError(f"local fixture did not become ready: {url}")


def seed(database: Path) -> None:
    projected: list[dict[str, JsonValue | datetime]] = []
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(8568):
        memory_id = f"mem-{index:05d}"
        memory_type = (
            "remove" if index >= 8565 else "decision" if index >= 5000 else "Decision"
        )
        metadata: dict[str, JsonValue] = {"app_id": "qa-app", "type": memory_type}
        CoreFixture.rows[memory_id] = {
            "id": memory_id,
            "memory": f"fixture {memory_id}",
            "user_id": "qa-user",
            "app_id": "qa-app",
            "metadata": metadata,
        }
        projected.append(
            {
                "id": memory_id,
                "project_id": "qa-project",
                "mem0_memory_id": memory_id,
                "user_id": "qa-user",
                "app_id": "qa-app",
                "metadata_projection_json": json.dumps(metadata),
                "created_at": stamp,
                "updated_at": stamp,
                "consolidation_state": "ACTIVE",
            }
        )
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        _ = connection.execute(insert(MemoryIndex), projected)
    engine.dispose()


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    sidecar = Path(os.environ["PI_QA_SIDECAR_SOURCE"])
    core = FixtureServer(("127.0.0.1", 0), CoreFixture)
    core.daemon_threads = True
    threading.Thread(target=core.serve_forever, daemon=True).start()
    core_url = f"http://127.0.0.1:{core.server_address[1]}"
    sidecar_port, mcp_port = free_port(), free_port()
    processes: list[subprocess.Popen[bytes]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="pi-chain-") as directory:
            root = Path(directory)
            database = root / "memories.sqlite3"
            environment = {
                **os.environ,
                "PYTHONPATH": str(sidecar / "src"),
                "MEM0_SIDECAR_DATABASE_URL": f"sqlite:///{database}",
                "MEM0_SIDECAR_MEM0_BASE_URL": core_url,
                "MEM0_SIDECAR_MEM0_API_KEY": "core-fixture-token",
                "MEM0_SIDECAR_DEFAULT_PROJECT_ID": "qa-project",
                "MEM0_SIDECAR_CLIENT_AUTH_ENABLED": "true",
                "MEM0_SIDECAR_CONSOLIDATION_ENABLED": "false",
                "MEM0_SIDECAR_DIRECT_WRITE_SYNC_ENABLED": "false",
            }
            with (
                (root / "sidecar.log").open("wb") as sidecar_log,
                (root / "mcp.log").open("wb") as mcp_log,
            ):
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "uvicorn",
                            "mem0_sidecar.http_adapter.app:create_app",
                            "--factory",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(sidecar_port),
                        ],
                        env=environment,
                        stdout=sidecar_log,
                        stderr=subprocess.STDOUT,
                    )
                )
                ready(f"http://127.0.0.1:{sidecar_port}/readyz")
                seed(database)
                mcp_url = f"http://127.0.0.1:{mcp_port}/mcp"
                environment.update(
                    {
                        "PYTHONPATH": str(repo / "src"),
                        "MEM0_OSS_MCP_HOST": "127.0.0.1",
                        "MEM0_OSS_MCP_PORT": str(mcp_port),
                        "MEM0_OSS_MCP_AUTH_MODE": "static",
                        "MEM0_OSS_MCP_TOKEN": "mcp-fixture-token",
                        "MEM0_OSS_BASE_URL": core_url,
                        "MEM0_OSS_MCP_URL": mcp_url,
                        "MEM0_SIDECAR_BASE_URL": f"http://127.0.0.1:{sidecar_port}",
                        "MEM0_SIDECAR_PROJECT_ID": "qa-project",
                        "MEM0_SIDECAR_API_KEY": "core-fixture-token",
                        "MEM0_SIDECAR_REQUIRED": "true",
                        "PI_QA_CORE_URL": core_url,
                    }
                )
                processes.append(
                    subprocess.Popen(
                        [sys.executable, "-m", "mem0_oss_mcp.server"],
                        env=environment,
                        stdout=mcp_log,
                        stderr=subprocess.STDOUT,
                    )
                )
                ready(f"http://127.0.0.1:{mcp_port}/health")
                result = subprocess.run(
                    ["bun", str(repo / "tests/pi_chain_qa.ts")],
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=180,
                )
                if result.returncode:
                    _ = sys.stderr.write(result.stderr)
                    raise AssertionError("real HTTP chain driver failed")
                print(result.stdout.strip())
                for process in reversed(processes):
                    process.terminate()
                    _ = process.wait(timeout=10)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.kill()
                _ = process.wait(timeout=10)
        core.shutdown()
        core.server_close()


if __name__ == "__main__":
    main()
