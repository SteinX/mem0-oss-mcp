from typing import Literal
from unittest.mock import patch

import pytest

from mem0_oss_mcp import server


@pytest.mark.parametrize("mode", ["cursor", "count"])
@pytest.mark.parametrize("field", ["user_id", "agent_id", "run_id", "app_id"])
@pytest.mark.parametrize("invalid", [0, False, {}, [], ""])
def test_rpc_rejects_malformed_scope_override_before_backend(
    mode: Literal["cursor", "count"],
    field: str,
    invalid: int | bool | dict[str, str] | list[str] | str,
) -> None:
    with (
        patch.object(server.Config, "sidecar_base_url", "http://fixture.invalid"),
        patch.object(server, "_sidecar_backend", return_value={
            "protocol": "cursor-v1", "results": [], "total": 0,
            "has_more": False, "next_cursor": None,
        }) as downstream,
    ):
        response = server.handle_rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "get_memories",
                    "arguments": {
                        "mode": mode,
                        "filters": {"app_id": "selected-app", field: "selected-id"},
                        field: invalid,
                    },
                },
            }
        )
        assert response is not None
        assert response["result"].get("isError") is True
        assert "non-empty string" in response["result"]["content"][0]["text"]
        downstream.assert_not_called()


def test_valid_override_preserves_explicit_scope_and_wildcard_semantics() -> None:
    payload = {
        "protocol": "cursor-v1", "results": [], "total": 0,
        "has_more": False, "next_cursor": None,
    }
    with (
        patch.object(server.Config, "sidecar_base_url", "http://fixture.invalid"),
        patch.object(server, "_sidecar_backend", return_value=payload) as downstream,
    ):
        server.get_memories({
            "mode": "count", "filters": {"app_id": "selected-app"},
            "app_id": "explicit-app", "user_id": "selected-user",
        })
        body = downstream.call_args.args[2]
        assert body["app_id"] == "explicit-app"
        assert body["filters"] == {"user_id": "selected-user"}
        assert "project_wide" not in body
        server.get_memories({"mode": "count", "app_id": "*"})
        assert downstream.call_args.args[2]["project_wide"] is True
