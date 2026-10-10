from unittest.mock import patch

import pytest

from mem0_oss_mcp import server


@pytest.mark.parametrize("mode,backend_mode", [("cursor", "page"), ("count", "count")])
def test_cursor_and_count_when_sidecar_enabled_use_scoped_scan(mode, backend_mode):
    # Given the authenticated sidecar route with a scope larger than a page window.
    payload = {
        "protocol": "cursor-v1",
        "results": [],
        "total": 8568,
        "count_basis": "sidecar_projection",
        "next_cursor": "next" if mode == "cursor" else None,
        "has_more": mode == "cursor",
        "stale_skipped": 0,
    }
    with (
        patch.object(server.Config, "sidecar_base_url", "http://fixture.invalid"),
        patch.object(server.Config, "sidecar_project_id", "fixture-project"),
        patch.object(server, "_sidecar_backend", return_value=payload) as downstream,
    ):
        # When one page or a count is requested through the existing MCP tool.
        result = server.get_memories(
            {
                "mode": mode,
                **({"cursor": "previous", "page_size": 20} if mode == "cursor" else {}),
                "filters": {
                    "user_id": "fixture-user",
                    "app_id": "fixture-app",
                    "type": "decision",
                },
            }
        )
    # Then one protected scan is used, preserving filters and projection count.
    assert downstream.call_args.args[:2] == ("POST", "/v1/memories/scan")
    body = downstream.call_args.args[2]
    assert body["mode"] == backend_mode
    assert body["project_id"] == "fixture-project"
    assert body["app_id"] == "fixture-app"
    assert body["filters"] == {"user_id": "fixture-user", "type": "decision"}
    assert result["protocol"] == "cursor-v1"
    assert result["total"] == 8568


def test_cursor_when_sidecar_disabled_requires_upgrade_instead_of_core_scan():
    # Given a Core-only bridge, when cursor traversal is requested, then fail before a Core scan.
    with (
        patch.object(server.Config, "sidecar_base_url", ""),
        patch.object(server, "_backend") as core,
    ):
        with pytest.raises(ValueError, match="sidecar"):
            server.get_memories(
                {"mode": "cursor", "filters": {"user_id": "fixture-user"}}
            )
        core.assert_not_called()


@pytest.mark.parametrize(
    "options",
    [
        {"mode": "count", "cursor": "next"},
        {"mode": "count", "page_size": 20},
        {"mode": "cursor", "filters": {"unsupported": "value"}},
    ],
)
def test_invalid_scan_options_fail_before_backend(options):
    with (
        patch.object(server.Config, "sidecar_base_url", "http://fixture.invalid"),
        patch.object(server, "_sidecar_backend") as downstream,
    ):
        with pytest.raises(ValueError):
            server.get_memories(options)
        downstream.assert_not_called()


def test_legacy_sidecar_response_is_not_mistaken_for_complete_cursor_listing():
    with (
        patch.object(server.Config, "sidecar_base_url", "http://fixture.invalid"),
        patch.object(server, "_sidecar_backend", return_value={"results": []}),
        pytest.raises(ValueError, match="0.3.13"),
    ):
        server.get_memories({"mode": "cursor"})


@pytest.mark.parametrize("value", ["false", "true", 0, 1, [], {}, None])
def test_malformed_include_expired_is_rejected_before_backend(value):
    with (
        patch.object(server.Config, "sidecar_base_url", "http://fixture.invalid"),
        patch.object(server, "_sidecar_backend") as downstream,
    ):
        with pytest.raises(TypeError, match="boolean"):
            server.get_memories({"mode": "count", "include_expired": value})
        downstream.assert_not_called()
