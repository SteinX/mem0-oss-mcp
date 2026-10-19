import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from mem0_oss_mcp import server

JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]


@pytest.fixture
def retrieval_backend() -> Iterator[tuple[str, list[JsonObject]]]:
    requests: list[JsonObject] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append(
                {
                    "path": self.path,
                    "body": json.loads(
                        self.rfile.read(int(self.headers["Content-Length"]))
                    ),
                }
            )
            rows = [
                {"id": "expired", "expiration_date": "2000-01-01"},
                {
                    "id": "current",
                    "memory": "Mem0 2.2.1 部署到 /workspace/data/mem0，排查 HTTP 502",
                    "score": 0.81,
                    "rerank_score": 0.97,
                    "score_details": {"vector": 0.81, "rerank": 0.97},
                },
                {"id": "extra", "score": 0.7},
            ]
            data = json.dumps({"results": rows, "count": 3}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: str) -> None:
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}", requests
        finally:
            httpd.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize("name", ["rerank", "explain", "current_only"])
def test_search_schema_advertises_optional_boolean_controls(name: str) -> None:
    # Given the public MCP tools list, when a client inspects search options.
    schema = next(
        tool for tool in server.tool_schema() if tool["name"] == "search_memories"
    )

    # Then the control is boolean and omission preserves the backend default.
    assert schema["inputSchema"]["properties"][name] == {"type": "boolean"}
    assert name not in schema["inputSchema"]["required"]


@pytest.mark.parametrize("target", ["core", "sidecar"])
@pytest.mark.parametrize(
    "controls",
    [
        {},
        {"rerank": True, "explain": True},
        {"rerank": False, "explain": False},
        {"rerank": True, "explain": False},
        {"rerank": False, "explain": True},
    ],
)
def test_search_controls_and_scores_survive_http_routing(
    retrieval_backend: tuple[str, list[JsonObject]],
    target: str,
    controls: JsonObject,
) -> None:
    # Given an HTTP backend with expired and scored retrieval candidates.
    base_url, requests = retrieval_backend
    with (
        patch.object(server.Config, "base_url", base_url),
        patch.object(server.Config, "api_key", "fixture-core-key"),
        patch.object(
            server.Config, "sidecar_base_url", base_url if target == "sidecar" else ""
        ),
        patch.object(server.Config, "sidecar_api_key", "fixture-sidecar-key"),
        patch.object(server.Config, "sidecar_project_id", "fixture-project"),
        patch.object(server.Config, "sidecar_required", False),
    ):
        # When the public MCP dispatcher searches with explicit or omitted controls.
        response = server.handle_rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "search_memories",
                    "arguments": {
                        "query": "2.2.1 /workspace/data/mem0 HTTP 502",
                        "top_k": 1,
                        "user_id": "fixture-user",
                        "app_id": "fixture-app",
                        "filters": {"AND": [{"agent_id": "fixture-agent"}]},
                        **controls,
                    },
                },
            }
        )

    # Then routing preserves controls/scope and filtering keeps complete scores.
    assert response is not None
    assert not response["result"].get("isError", False), response
    assert requests == [
        {
            "path": "/v3/memories/search" if target == "sidecar" else "/search",
            "body": {
                "query": "2.2.1 /workspace/data/mem0 HTTP 502",
                "top_k": 11,
                "filters": {
                    "user_id": "fixture-user",
                    "agent_id": "fixture-agent",
                    "app_id": "fixture-app",
                },
                **controls,
                **(
                    {"project_id": "fixture-project", "app_id": "fixture-app"}
                    if target == "sidecar"
                    else {}
                ),
            },
        }
    ]
    assert json.loads(response["result"]["content"][0]["text"]) == {
        "results": [
            {
                "id": "current",
                "memory": "Mem0 2.2.1 部署到 /workspace/data/mem0，排查 HTTP 502",
                "score": 0.81,
                "rerank_score": 0.97,
                "score_details": {"vector": 0.81, "rerank": 0.97},
            }
        ],
        "count": 1,
    }


@pytest.mark.parametrize("name", ["rerank", "explain", "current_only"])
@pytest.mark.parametrize("value", [None, 0, 1, "false", "true", [], {}])
def test_search_rejects_non_boolean_controls_before_backend(
    name: str,
    value: JsonValue,
) -> None:
    # Given untrusted tool input and no permitted backend request.
    with patch.object(
        server, "_uses_sidecar", side_effect=AssertionError("backend reached")
    ):
        # When search receives a non-boolean control, then it rejects the input.
        with pytest.raises(TypeError, match=f"{name} must be a boolean"):
            server.search_memories({"query": "HTTP 502", name: value})


def test_sidecar_history_and_event_controls_survive_http(retrieval_backend):
    base_url, requests = retrieval_backend
    with (
        patch.object(server.Config, "sidecar_base_url", base_url),
        patch.object(server.Config, "sidecar_project_id", "fixture-project"),
    ):
        result = server.search_memories({
            "query": "version", "app_id": "fixture-app", "top_k": 100,
            "current_only": False, "event_from": "2020-01-01", "event_to": "2020-12-31",
            "latest_only": True,
        })
    assert result["results"][0]["id"] == "current"
    body = requests[0]["body"]
    assert body["top_k"] == 200
    assert body["current_only"] is False
    assert body["latest_only"] is True
    assert body["event_from"] == "2020-01-01" and body["event_to"] == "2020-12-31"


@pytest.mark.parametrize("controls", [
    {"current_only": False}, {"event_from": "2020-01-01"},
])
def test_sidecar_controls_are_rejected_in_direct_core_mode(controls):
    with patch.object(server.Config, "sidecar_base_url", ""):
        with pytest.raises(ValueError, match="sidecar-backed"):
            server.search_memories({"query": "version", **controls})


@pytest.mark.parametrize("controls", [
    {"event_from": "2020-02-30"}, {"event_from": None},
    {"event_to": 20200101}, {"event_to": "20200101"},
    {"event_from": "2026-01-01", "event_to": "2020-01-01"},
])
def test_invalid_event_dates_are_rejected_before_routing(controls):
    with patch.object(server, "_uses_sidecar", side_effect=AssertionError("routed")):
        with pytest.raises(ValueError):
            server.search_memories({"query": "version", **controls})
