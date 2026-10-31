from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from mem0_oss_mcp import server
from mem0_oss_mcp.auth import AuthPrincipal
from mem0_oss_mcp.caller_context import CALLER_CONTEXT_HEADER, bind_http_principal


OWNER = "e0544e3c-d217-40d9-bc9a-c1f64077542a"
OTHER = "8f7ebdcc-0df0-42e7-8f43-e7db627c9788"


def principal(credential_id: str | None = OWNER):
    return AuthPrincipal(
        mechanism="core_api_key", credential_kind="core_api_key",
        credential_id=credential_id, role="member",
    )


def channel(credential_id=OWNER):
    return {"transport": "mcp", "credential_kind": "core_api_key", "credential_id": credential_id}


def event(credential_id=OWNER):
    return {
        "id": "persisted-event", "status": "FAILED", "operation": "memory.add",
        "requested_at": "2026-10-01T00:00:00Z", "channel": channel(credential_id),
        "result_count": 0, "error": {"message": "extraction failed"},
    }


def page(rows):
    return {"results": rows, "total": len(rows), "page": 1, "page_size": 10, "has_more": False}


def test_events_survive_cache_loss_and_scope_before_pagination():
    with (
        patch.object(server.Config, "sidecar_base_url", "http://sidecar.test"),
        patch.object(server.Config, "sidecar_project_id", "configured-project"),
        patch.dict(server.EVENTS, {}, clear=True),
        patch.object(server, "_sidecar_backend", return_value=page([event()])) as backend,
        bind_http_principal(principal()),
    ):
        result = server.list_events({
            "page_size": 10, "app_id": "requested-app", "user_id": "alice",
            "statuses": ["FAILED"], "operation": "ADD",
            "project_id": "untrusted-project", "channel": channel(OTHER),
        })
    assert backend.call_args.args == ("POST", "/v1/events/query", {
        "project_id": "configured-project", "app_id": "requested-app",
        "page": 1, "page_size": 10, "entity_filters": {"user_id": "alice"},
        "statuses": ["FAILED"], "operation": "ADD", "channel": channel(),
    })
    assert result["count"] == 1
    assert result["results"][0]["event_id"] == "persisted-event"
    assert result["results"][0]["error"] == {"message": "extraction failed"}
    assert "channel" not in result["results"][0]


def test_events_default_to_credential_owned_project_wide_scope():
    with (
        patch.object(server.Config, "sidecar_base_url", "http://sidecar.test"),
        patch.object(server, "_sidecar_backend", return_value=page([])) as backend,
        bind_http_principal(AuthPrincipal(mechanism="static", credential_kind="legacy_static")),
    ):
        server.list_events({})
    body = backend.call_args.args[2]
    assert body["project_wide"] is True
    assert "app_id" not in body
    assert body["channel"] == {"transport": "mcp", "credential_kind": "legacy_static", "credential_id": None}


@pytest.mark.parametrize("record", [event(OTHER), {**event(), "channel": None}])
def test_events_fail_closed_if_backend_ignores_credential_filter(record):
    with (
        patch.object(server.Config, "sidecar_base_url", "http://sidecar.test"),
        patch.object(server, "_sidecar_backend", return_value=page([record])),
        bind_http_principal(principal()),
        pytest.raises(server.BackendError, match="credential scope"),
    ):
        server.list_events({})


def test_missing_credential_id_cannot_broaden_event_list():
    with (
        patch.object(server.Config, "sidecar_base_url", "http://sidecar.test"),
        patch.object(server, "_sidecar_backend") as backend,
        bind_http_principal(principal(None)),
        pytest.raises(server.BackendError, match="credential ID"),
    ):
        server.list_events({})
    backend.assert_not_called()


def test_entity_list_honors_requested_app():
    with (
        patch.object(server.Config, "sidecar_base_url", "http://sidecar.test"),
        patch.object(server, "_sidecar_backend", return_value={"results": [], "total": 0}) as backend,
    ):
        assert server.list_entities({"app_id": "requested-app"}) == []
    assert len(backend.call_args_list) == 3
    assert all(call.args[2]["app_id"] == "requested-app" for call in backend.call_args_list)


def test_direct_core_retains_local_events_and_rejects_unsupported_filters():
    with (
        patch.object(server.Config, "sidecar_base_url", ""),
        patch.dict(server.EVENTS, {"one": {"event_id": "one", "created_at": 1}}, clear=True),
    ):
        assert server.list_events({})["results"][0]["event_id"] == "one"
        with pytest.raises(ValueError, match="sidecar"):
            server.list_events({"app_id": "a"})


def test_authenticated_event_query_uses_real_sidecar(tmp_path, monkeypatch):
    source = os.environ.get("MEM0_COMPAT_SIDECAR_ROOT")
    if not source:
        pytest.skip("set MEM0_COMPAT_SIDECAR_ROOT for real Sidecar contract verification")
    monkeypatch.syspath_prepend(os.path.join(source, "src"))
    from fastapi.testclient import TestClient
    from mem0_sidecar.config import SidecarSettings
    from mem0_sidecar.http_adapter.app import create_app
    from mem0_sidecar.request_attribution import RequestAttribution, bind_request_attribution
    from mem0_sidecar.store.repositories import EventRepository, ProjectRepository

    app = create_app(settings=SidecarSettings(
        database_url=f"sqlite:///{tmp_path / 'events.sqlite3'}",
        mem0_base_url="http://unused.test", default_project_id="project",
        mem0_api_key="fixture-operator-key", client_auth_enabled=True,
    ))
    with app.state.session_factory() as session:
        ProjectRepository(session).upsert_default_project(
            project_id="project", name="project", mem0_base_url="http://unused.test",
            default_app_id="default-app",
        )
        for credential_id, app_id in [(OWNER, "app-a"), (OWNER, "app-b"), (OTHER, "app-a")]:
            with bind_request_attribution(RequestAttribution(
                transport="mcp",
                credential_kind="core_api_key",
                credential_id=credential_id,
                credential_label="Fixture API key",
                credential_prefix="fixture",
            )):
                recorded = EventRepository(session).create_event(
                    project_id="project", app_id=app_id, user_id="alice",
                    operation="memory.add", request={"messages": [], "app_id": app_id},
                )
                EventRepository(session).mark_succeeded(recorded.id, response={"results": []})
        session.commit()

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.body

    with TestClient(app) as client:
        def transport(request, timeout):
            headers = dict(request.header_items())
            assert CALLER_CONTEXT_HEADER.lower() not in {key.lower() for key in headers}
            response = client.post("/v1/events/query", json=json.loads(request.data), headers=headers)
            assert response.status_code == 200, response.text
            return Response(response.content)

        with (
            patch.object(server.Config, "sidecar_base_url", "http://sidecar.test"),
            patch.object(server.Config, "sidecar_project_id", "project"),
            patch.object(server.Config, "sidecar_api_key", "fixture-operator-key"),
            patch.object(server, "_open_no_redirect", side_effect=transport),
            patch.dict(server.EVENTS, {}, clear=True),
            bind_http_principal(principal()),
        ):
            first = server.list_events({"page_size": 1})
            second = server.list_events({"page_size": 1, "page": 2})
            assert first["count"] == second["count"] == 2
            assert first["has_more"] is True and second["has_more"] is False
            assert first["results"][0]["event_id"] != second["results"][0]["event_id"]
            assert server.list_events({"app_id": "app-a"})["count"] == 1
