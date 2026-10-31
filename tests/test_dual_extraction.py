from __future__ import annotations

import json
import os
import uuid
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from mem0_oss_mcp import server
from mem0_oss_mcp.auth import AuthPrincipal
from mem0_oss_mcp.caller_context import bind_http_principal

OWNER = "e0544e3c-d217-40d9-bc9a-c1f64077542a"
OTHER = "8f7ebdcc-0df0-42e7-8f43-e7db627c9788"


def principal(credential_id=OWNER):
    return AuthPrincipal(
        mechanism="core_api_key",
        credential_kind="core_api_key",
        credential_id=credential_id,
        credential_label="dual-test",
        credential_prefix="m0sk_dual",
        role="member",
    )


def request():
    return {
        "messages": [{"role": "user", "content": "A verified decision"}],
        "user_id": "alice", "agent_id": "repository", "app_id": "app", "run_id": "run",
        "infer": True, "extraction_mode": "dual", "idempotency_key": "capture-1",
        "custom_instructions": "personal", "agent_custom_instructions": "project",
        "custom_categories": [{"decision": "Verified decision"}],
    }


def test_partial_reply_loss_reuses_both_keys_and_composite_survives_restart():
    writes = {}
    events = {}
    lost = False

    def backend(method, path, body=None, query=None, *, idempotency_key=None):
        nonlocal lost
        if path == "/v1/extraction-capabilities":
            return {"protocol": "request-profiles-v1", "dual_extraction_enabled": True}
        if method == "GET":
            return events[path.rsplit("/", 1)[-1]]
        fingerprint = json.dumps(body, sort_keys=True)
        if idempotency_key in writes:
            previous, receipt = writes[idempotency_key]
            if fingerprint != previous:
                raise server.BackendError(409, "different request")
            return receipt
        event_id = str(uuid.uuid4())
        receipt = {"event": {"id": event_id}, "memory": {"results": [{"id": event_id}]}}
        writes[idempotency_key] = (fingerprint, receipt)
        events[event_id] = {"id": event_id, "status": "SUCCEEDED", "result_count": 1}
        if (
            body is not None
            and body["extraction_track"] == "project"
            and not lost
        ):
            lost = True
            raise server.BackendError(503, "reply lost after commit")
        return receipt

    with (
        patch.object(server.Config, "sidecar_base_url", "http://sidecar.test"),
        patch.object(server, "_sidecar_backend", side_effect=backend),
        patch.dict(server.EVENTS, {}, clear=True),
    ):
        with pytest.raises(server.BackendError, match="incomplete"):
            server.add_memory(request())
        server.EVENTS.clear()
        receipt = server.add_memory(request())
        assert len(writes) == 2
        bodies = [json.loads(row[0]) for row in writes.values()]
        assert bodies[0]["user_id"] == "alice" and "agent_id" not in bodies[0]
        assert bodies[1]["agent_id"] == "repository" and "user_id" not in bodies[1]
        assert all(row["app_id"] == "app" and row["run_id"] == "run" for row in bodies)
        server.EVENTS.clear()
        status = server.get_event_status(receipt)
        assert status is not None
        assert status["status"] == "SUCCEEDED" and status["result_count"] == 2
        with pytest.raises(server.BackendError, match="different request"):
            server.add_memory({**request(), "agent_custom_instructions": "changed"})
        assert len(writes) == 2


def test_disabled_dual_policy_and_missing_scope_fail_before_writes():
    with (
        patch.object(server.Config, "sidecar_base_url", "http://sidecar.test"),
        patch.object(server, "_sidecar_backend", return_value={"dual_extraction_enabled": False}) as backend,
    ):
        with pytest.raises(ValueError, match="not enabled"):
            server.add_memory(request())
        assert backend.call_count == 1
        with pytest.raises(ValueError, match="agent_id"):
            server.add_memory({**request(), "agent_id": "bad scope"})
        assert backend.call_count == 1


def test_direct_writes_and_default_single_do_not_split():
    with (
        patch.object(server.Config, "extraction_mode", "single"),
        patch.object(server, "_add_single_memory", return_value={"event_id": "one"}) as single,
        patch.object(server, "_add_dual_memory") as dual,
    ):
        server.add_memory({**request(), "infer": False})
        args = request()
        args.pop("extraction_mode")
        server.add_memory(args)
        assert single.call_count == 2
        dual.assert_not_called()


def test_dual_extraction_against_authenticated_sidecar(tmp_path, monkeypatch):
    source = os.environ.get("MEM0_DUAL_SIDECAR_ROOT")
    if not source:
        pytest.skip("set MEM0_DUAL_SIDECAR_ROOT for real dual Sidecar verification")
    monkeypatch.syspath_prepend(os.path.join(source, "src"))
    from fastapi.testclient import TestClient
    from mem0_sidecar.config import SidecarSettings
    from mem0_sidecar.core.extraction_policy import (
        ExtractionSettings,
        set_extraction_policy,
    )
    from mem0_sidecar.http_adapter.app import create_app
    from mem0_sidecar.store.models import Event

    class Core:
        def __init__(self):
            self.writes = []
            self.records = {}

        async def add_memory(self, payload):
            self.writes.append(payload)
            memory_id = f"memory-{len(self.writes)}"
            categories = payload.get("extraction_policy", {}).get("categories", [])
            record = {
                "id": memory_id,
                "memory": payload.get("text", "verified fact"),
                "metadata": {
                    **payload.get("metadata", {}),
                    "categories": [category["name"] for category in categories],
                },
            }
            self.records[memory_id] = record
            return {"results": [record]}

        async def get_memory(self, memory_id):
            return self.records[memory_id]

    core = Core()
    app = create_app(
        settings=SidecarSettings(
            database_url=f"sqlite:///{tmp_path / 'dual.sqlite3'}",
            default_project_id="project",
            mem0_base_url="http://core.invalid",
            mem0_api_key="fixture-operator-key",
            client_auth_enabled=True,
        ),
        mem0_client=core,
    )
    with app.state.session_factory() as session:
        set_extraction_policy(
            session, project_id="project", app_id="app", expected_version=0,
            settings=ExtractionSettings(
                enabled=True, allow_request_profiles=True,
                dual_extraction_enabled=True,
            ),
        )
        session.commit()

    class Response:
        def __init__(self, body):
            self.body = body
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self) -> bytes:
            return self.body

    lost = False
    write_requests = 0
    with TestClient(app) as client:
        def transport(request, timeout):
            nonlocal lost, write_requests
            del timeout
            is_write = (
                request.get_method() == "POST"
                and "/memories/add" in request.full_url
            )
            if is_write:
                write_requests += 1
            response = client.request(
                request.get_method(),
                request.full_url,
                headers=dict(request.header_items()),
                content=request.data,
            )
            if is_write and write_requests == 2 and not lost:
                lost = True
                raise OSError("simulated response loss after commit")
            if response.status_code >= 400:
                raise HTTPError(
                    request.full_url,
                    response.status_code,
                    response.text,
                    response.headers,
                    BytesIO(response.content),
                )
            return Response(response.content)

        with (
            patch.object(server.Config, "sidecar_base_url", "http://sidecar.test"),
            patch.object(server.Config, "sidecar_project_id", "project"),
            patch.object(server.Config, "sidecar_api_key", "fixture-operator-key"),
            patch.object(server, "_open_no_redirect", side_effect=transport),
            patch.dict(server.EVENTS, {}, clear=True),
            bind_http_principal(principal()),
        ):
            with pytest.raises(server.BackendError, match="incomplete"):
                server.add_memory(request())
            assert len(core.writes) == 2
            server.EVENTS.clear()
            receipt = server.add_memory(request())
            assert len(core.writes) == 2
            assert receipt["event_id"].startswith("dual:")
            assert {bool(row.get("user_id")) for row in core.writes} == {True, False}
            assert {bool(row.get("agent_id")) for row in core.writes} == {True, False}
            assert all(
                row["metadata"].get("memory_track") in {"personal", "project"}
                for row in core.writes
            )
            status = server.get_event_status(receipt)
            assert status["status"] == "SUCCEEDED"
            with pytest.raises(server.BackendError, match="409|different"):
                server.add_memory({**request(), "custom_instructions": "changed"})
            with app.state.session_factory() as session:
                events = session.query(Event).all()
                assert len(events) == 2
                assert {event.credential_id for event in events} == {OWNER}
                assert all(
                    event.app_id == "app" and event.run_id == "run"
                    for event in events
                )
                assert {(event.user_id, event.agent_id) for event in events} == {
                    ("alice", None), (None, "repository")
                }
            with (
                bind_http_principal(principal(OTHER)),
                pytest.raises(server.BackendError, match="not found|credential"),
            ):
                server.get_event_status(receipt)
