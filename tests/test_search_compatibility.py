import pathlib
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mem0_oss_mcp import server


@pytest.mark.parametrize("sidecar", [True, False])
@pytest.mark.parametrize("enabled", [True, False])
def test_search_preserves_latest_only_and_rerank(monkeypatch, sidecar, enabled):
    backend = Mock(return_value={"results": []})
    monkeypatch.setattr(server, "_uses_sidecar", lambda: sidecar)
    monkeypatch.setattr(server, "_sidecar_backend", backend)
    monkeypatch.setattr(server, "_backend", backend)

    server.search_memories({"query": "current state", "latest_only": enabled, "rerank": enabled})

    forwarded = backend.call_args.args[2]
    assert forwarded["latest_only"] is enabled
    assert forwarded["rerank"] is enabled


@pytest.mark.parametrize("value", ["false", 1, None])
def test_latest_only_rejects_non_boolean_values_before_calling_backend(monkeypatch, value):
    backend = Mock()
    monkeypatch.setattr(server, "_backend", backend)
    monkeypatch.setattr(server, "_sidecar_backend", backend)

    with pytest.raises(TypeError, match="latest_only"):
        server.search_memories({"query": "current state", "latest_only": value})

    backend.assert_not_called()


def test_tool_schema_exposes_latest_only():
    search = next(tool for tool in server.tool_schema() if tool["name"] == "search_memories")

    assert search["inputSchema"]["properties"]["latest_only"]["type"] == "boolean"
