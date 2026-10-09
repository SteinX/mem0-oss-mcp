from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from mem0_oss_mcp import server
from mem0_oss_mcp.auth import AuthPrincipal
from mem0_oss_mcp.caller_context import CALLER_CONTEXT_HEADER, bind_http_principal


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"event":{"id":"sidecar-event-one"},"memory":{"results":[{"id":"memory-one","event":"ADD"}]}}'


def test_add_idempotency_is_sent_as_sidecar_header() -> None:
    with (
        patch.object(server.Config, "sidecar_base_url", "http://sidecar.test"),
        patch.object(server, "_open_no_redirect", return_value=Response()) as transport,
    ):
        event = server.add_memory({
            "text": "packet contents", "user_id": "root", "app_id": "mem0",
            "idempotency_key": "codex-packet-one", "infer": False,
        })
    request = transport.call_args.args[0]
    assert request.get_header("Idempotency-key") == "codex-packet-one"
    assert "idempotency_key" not in json.loads(request.data)
    assert event["status"] == "SUCCEEDED"
    assert event["event_id"] == "sidecar-event-one"
    assert server.get_event_status({"event_id": event["event_id"]})["results"] == [{"id": "memory-one", "event": "ADD"}]


def test_event_poll_survives_bridge_restart() -> None:
    with (
        patch.dict(server.EVENTS, {}, clear=True),
        patch.object(server.Config, "sidecar_base_url", "http://sidecar.test"),
        patch.object(server, "_sidecar_backend", return_value={
            "id": "persisted-event", "status": "SUCCEEDED", "subject_id": "memory-one",
            "result_previews": [{"id": "memory-one", "memory": "recorded"}], "result_count": 1,
        }) as transport,
    ):
        event = server.get_event_status({"event_id": "persisted-event"})
    assert event["status"] == "SUCCEEDED"
    assert event["memory_id"] == "memory-one"
    assert len(event["results"]) == 1
    assert transport.call_args.args[:2] == ("GET", "/v1/event/persisted-event")
    assert transport.call_args.kwargs["query"] == {"project_id": server.Config.sidecar_project_id, "project_wide": True}


def test_persisted_status_read_keeps_private_operator_authorization() -> None:
    with (
        patch.object(server.Config, "sidecar_base_url", "http://sidecar.test"),
        patch.object(server.Config, "sidecar_api_key", "fixture-operator-key"),
        patch.object(server, "_open_no_redirect", return_value=Response()) as transport,
        bind_http_principal(AuthPrincipal(
            mechanism="core_api_key", role="admin", credential_kind="core_api_key",
        )),
    ):
        server._sidecar_backend("GET", "/v1/event/persisted-event")
    headers = {key.lower(): value for key, value in transport.call_args.args[0].header_items()}
    assert headers["x-api-key"] == "fixture-operator-key"
    assert CALLER_CONTEXT_HEADER.lower() not in headers


@pytest.mark.parametrize("key", ["", "bad key", "bad\nkey", "x" * 129, 123])
def test_invalid_key_never_reaches_backend(key: object) -> None:
    with patch.object(server, "_open_no_redirect") as transport:
        with pytest.raises(ValueError):
            server.add_memory({"text": "packet", "idempotency_key": key})
    transport.assert_not_called()


def test_idempotent_add_requires_durable_sidecar() -> None:
    with patch.object(server.Config, "sidecar_base_url", ""), patch.object(server, "_backend") as transport:
        with pytest.raises(ValueError, match="sidecar"):
            server.add_memory({"text": "packet", "idempotency_key": "packet-one"})
    transport.assert_not_called()
