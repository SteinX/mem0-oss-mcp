import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

from mem0_oss_mcp import server
from mem0_oss_mcp.auth import (
    AuthConfigurationError,
    AuthPrincipal,
    AuthUnavailable,
    McpAuthenticator,
    Unauthorized,
)
from mem0_oss_mcp.caller_context import encode_current_caller_context


class StubAuthenticator:
    def __init__(self, outcome):
        self.outcome = outcome

    def authenticate(self, authorization):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class QuietHandler(server.Handler):
    def log_message(self, format, *args):
        return


@contextmanager
def running_server(authenticator, *, sidecar_base_url=""):
    with (
        patch.object(server.Config, "authenticator", authenticator),
        patch.object(
            server.Config,
            "sidecar_base_url",
            sidecar_base_url,
        ),
        patch.object(server.Config, "sidecar_required", False),
        patch.object(server, "_backend", return_value={}),
    ):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}"
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            httpd.server_close()


def post_initialize(base_url, token="client-token"):
    request = urllib.request.Request(
        f"{base_url}/mcp",
        method="POST",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), json.loads(exc.read())


def post_raw(base_url, *, body, content_length):
    request = urllib.request.Request(
        f"{base_url}/mcp",
        method="POST",
        data=body,
        headers={
            "Authorization": "Bearer client-token",
            "Content-Type": "application/json",
            "Content-Length": str(content_length),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def post_payload(base_url, payload):
    encoded = json.dumps(payload).encode("utf-8")
    return post_raw(
        base_url,
        body=encoded,
        content_length=len(encoded),
    )


def admin_principal():
    return AuthPrincipal(
        mechanism="core_api_key",
        subject="8f7ebdcc-0df0-42e7-8f43-e7db627c9788",
        role="admin",
        credential_kind="core_api_key",
        credential_id="e0544e3c-d217-40d9-bc9a-c1f64077542a",
        credential_label="codex-devbox",
        credential_prefix="m0sk_client_",
    )


@pytest.mark.parametrize("idempotency_key", [None, "fixture-add-key"])
def test_authenticated_add_without_operator_key_does_not_persist(idempotency_key):
    arguments = {"text": "fixture memory"}
    if idempotency_key is not None:
        arguments["idempotency_key"] = idempotency_key
    with (
        patch.object(server.Config, "sidecar_api_key", ""),
        patch.dict(server.EVENTS, {}, clear=True),
        patch.object(
            server, "_sidecar_backend", return_value={"memory": {"id": "persisted"}}
        ) as write,
        running_server(
            StubAuthenticator(admin_principal()), sidecar_base_url="http://sidecar.test"
        ) as base_url,
    ):
        status, body = post_payload(
            base_url,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "add_memory", "arguments": arguments}},
        )
        write.assert_not_called()
        assert server.EVENTS == {}
    assert status == 200
    assert body["result"]["isError"] is True
    assert "503" in json.dumps(body["result"])


def test_disabled_auth_keyless_sidecar_add_event_reads_and_replay_over_loopback():
    authenticator = McpAuthenticator.from_env(
        base_url="",
        env={
            "MEM0_OSS_MCP_AUTH_MODE": "disabled",
            "MEM0_OSS_MCP_HOST": "127.0.0.1",
        },
    )
    persisted_event = {
        "id": "disabled-local-event",
        "status": "SUCCEEDED",
        "subject_id": "memory-one",
        "result_previews": [{"id": "memory-one", "memory": "local memory"}],
        "result_count": 1,
    }

    def sidecar_backend(method, path, body=None, query=None, **kwargs):
        if method == "POST":
            return {
                "event": {"id": persisted_event["id"]},
                "memory": {"results": [{"id": "memory-one", "event": "ADD"}]},
            }
        assert method == "GET"
        assert path == "/v1/event/disabled-local-event"
        return persisted_event

    def call_tool(base_url, request_id, name, arguments):
        status, body = post_payload(
            base_url,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        assert status == 200
        assert body["result"].get("isError") is not True
        return json.loads(body["result"]["content"][0]["text"])

    with (
        patch.object(server.Config, "sidecar_api_key", ""),
        patch.dict(server.EVENTS, {}, clear=True),
        patch.object(server, "_sidecar_backend", side_effect=sidecar_backend) as transport,
        running_server(authenticator, sidecar_base_url="http://sidecar.test") as base_url,
    ):
        added = call_tool(
            base_url,
            1,
            "add_memory",
            {"text": "local memory", "idempotency_key": "local-add-one"},
        )
        assert added == {"event_id": "disabled-local-event", "status": "SUCCEEDED"}

        listed = call_tool(base_url, 2, "list_events", {})
        assert [event["event_id"] for event in listed["results"]] == [
            "disabled-local-event"
        ]

        server.EVENTS.clear()
        status = call_tool(
            base_url,
            3,
            "get_event_status",
            {"event_id": "disabled-local-event"},
        )
        assert status["event_id"] == "disabled-local-event"
        assert status["results"] == persisted_event["result_previews"]

        replayed = call_tool(
            base_url,
            4,
            "add_memory",
            {"text": "local memory", "idempotency_key": "local-add-one"},
        )
        assert replayed == added
        assert transport.call_count == 3


def test_authenticated_request_reaches_json_rpc():
    authenticator = StubAuthenticator(
        AuthPrincipal(
            mechanism="core_api_key",
            subject="8f7ebdcc-0df0-42e7-8f43-e7db627c9788",
            role="admin",
            credential_kind="core_api_key",
            credential_id="e0544e3c-d217-40d9-bc9a-c1f64077542a",
            credential_label="codex-devbox",
            credential_prefix="m0sk_client_",
        )
    )

    with running_server(authenticator) as base_url:
        status, _, body = post_initialize(base_url)

    assert status == 200
    assert body["result"]["serverInfo"]["name"] == "mem0-oss-mcp"


def test_authenticated_principal_is_bound_during_json_rpc_dispatch():
    principal = AuthPrincipal(
        mechanism="core_api_key",
        subject="8f7ebdcc-0df0-42e7-8f43-e7db627c9788",
        role="admin",
        credential_kind="core_api_key",
        credential_id="e0544e3c-d217-40d9-bc9a-c1f64077542a",
        credential_label="codex-devbox",
        credential_prefix="m0sk_client_",
    )
    original_handle_rpc = server.handle_rpc
    observed = []

    def capture_context(message):
        observed.append(encode_current_caller_context())
        return original_handle_rpc(message)

    with (
        patch.object(server, "handle_rpc", side_effect=capture_context),
        running_server(StubAuthenticator(principal)) as base_url,
    ):
        status, _, _ = post_initialize(base_url)

    assert status == 200
    assert observed[0] is not None


def test_rejected_request_returns_bearer_401():
    with running_server(
        StubAuthenticator(Unauthorized("credential rejected"))
    ) as base_url:
        status, headers, body = post_initialize(base_url)

    assert status == 401
    assert headers["WWW-Authenticate"] == "Bearer"
    assert body == {"error": "unauthorized"}


def test_auth_backend_failure_returns_sanitized_503():
    with running_server(
        StubAuthenticator(AuthUnavailable("client authentication service unavailable"))
    ) as base_url:
        status, _, body = post_initialize(base_url, "m0sk_must_not_leak")

    assert status == 503
    assert body == {"error": "authentication unavailable"}
    assert "m0sk_must_not_leak" not in json.dumps(body)


def test_mcp_rejects_oversized_request_before_reading_body():
    principal = AuthPrincipal(
        mechanism="core_api_key",
        subject="8f7ebdcc-0df0-42e7-8f43-e7db627c9788",
        role="admin",
        credential_kind="core_api_key",
        credential_id="e0544e3c-d217-40d9-bc9a-c1f64077542a",
        credential_label="codex-devbox",
        credential_prefix="m0sk_client_",
    )

    with running_server(StubAuthenticator(principal)) as base_url:
        status, body = post_raw(
            base_url,
            body=b"{}",
            content_length=server._MAX_MCP_REQUEST_BYTES + 1,
        )

    assert status == 413
    assert body == {"error": "request too large"}


@pytest.mark.parametrize("content_length", ["invalid", "-1"])
def test_mcp_rejects_invalid_content_length(content_length):
    principal = AuthPrincipal(
        mechanism="core_api_key",
        subject="8f7ebdcc-0df0-42e7-8f43-e7db627c9788",
        role="admin",
        credential_kind="core_api_key",
        credential_id="e0544e3c-d217-40d9-bc9a-c1f64077542a",
        credential_label="codex-devbox",
        credential_prefix="m0sk_client_",
    )

    with running_server(StubAuthenticator(principal)) as base_url:
        status, body = post_raw(
            base_url,
            body=b"{}",
            content_length=content_length,
        )

    assert status == 400
    assert body["error"]["code"] == -32700


@pytest.mark.parametrize("payload", [None, 1, "request", []])
def test_mcp_returns_invalid_request_for_scalar_or_empty_batch(payload):
    with running_server(StubAuthenticator(admin_principal())) as base_url:
        status, body = post_payload(base_url, payload)

    assert status == 400
    assert body["error"] == {
        "code": -32600,
        "message": "invalid request",
    }


def test_mcp_mixed_batch_returns_invalid_item_and_valid_response():
    with running_server(StubAuthenticator(admin_principal())) as base_url:
        status, body = post_payload(
            base_url,
            [
                None,
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "initialize",
                    "params": {},
                },
            ],
        )

    assert status == 200
    assert body[0]["error"]["code"] == -32600
    assert body[1]["id"] == 7
    assert body[1]["result"]["serverInfo"]["name"] == "mem0-oss-mcp"


def test_mcp_all_notification_batch_returns_no_response_body():
    payload = json.dumps(
        [
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {},
            },
        ]
    ).encode("utf-8")
    with running_server(StubAuthenticator(admin_principal())) as base_url:
        request = urllib.request.Request(
            f"{base_url}/mcp",
            method="POST",
            data=payload,
            headers={
                "Authorization": "Bearer client-token",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            status = response.status
            body = response.read()

    assert status == 202
    assert body == b""


def test_mcp_rejects_over_limit_batch_before_dispatch():
    payload = [
        {
            "jsonrpc": "2.0",
            "id": index,
            "method": "initialize",
            "params": {},
        }
        for index in range(server._MAX_MCP_BATCH_ITEMS + 1)
    ]

    with (
        patch.object(server, "handle_rpc") as dispatch,
        running_server(StubAuthenticator(admin_principal())) as base_url,
    ):
        status, body = post_payload(base_url, payload)

    assert status == 400
    assert body["error"]["code"] == -32600
    dispatch.assert_not_called()


def test_health_does_not_require_client_credentials():
    with running_server(
        StubAuthenticator(Unauthorized("credential rejected"))
    ) as base_url:
        with urllib.request.urlopen(f"{base_url}/health", timeout=5) as response:
            body = json.loads(response.read())
            status = response.status

    assert status == 200
    assert body["status"] == "ok"


def test_health_is_side_effect_free_when_sidecar_is_configured():
    with (
        patch.object(
            server,
            "_sidecar_backend",
            return_value={"status": "ok"},
        ) as sidecar_backend,
        patch.object(
            server,
            "_send_sidecar_heartbeat",
            return_value={"ready": True},
        ) as heartbeat,
        running_server(
            StubAuthenticator(Unauthorized("credential rejected")),
            sidecar_base_url="http://sidecar.internal",
        ) as base_url,
    ):
        with urllib.request.urlopen(
            f"{base_url}/health",
            timeout=5,
        ) as response:
            body = json.loads(response.read())

    assert body == {"status": "ok"}
    sidecar_backend.assert_called_once_with("GET", "/readyz")
    heartbeat.assert_not_called()


def test_health_reports_live_sidecar_failure_without_writing_heartbeat():
    with (
        patch.object(
            server,
            "_sidecar_backend",
            side_effect=server.BackendError(502, "sidecar unavailable"),
        ) as sidecar_backend,
        patch.object(server, "_send_sidecar_heartbeat") as heartbeat,
        running_server(
            StubAuthenticator(Unauthorized("credential rejected")),
            sidecar_base_url="http://sidecar.internal",
        ) as base_url,
        pytest.raises(urllib.error.HTTPError) as captured,
    ):
        urllib.request.urlopen(f"{base_url}/health", timeout=5)

    assert captured.value.code == 502
    assert json.loads(captured.value.read()) == {
        "status": "error",
        "error": "backend unavailable",
    }
    sidecar_backend.assert_called_once_with("GET", "/readyz")
    heartbeat.assert_not_called()


def test_invalid_auth_configuration_fails_before_binding_listener():
    with (
        patch.object(
            McpAuthenticator,
            "from_env",
            side_effect=AuthConfigurationError("auth configuration invalid"),
        ),
        patch.object(server, "ThreadingHTTPServer") as listener,
        pytest.raises(SystemExit, match="auth configuration invalid"),
    ):
        server.main()

    listener.assert_not_called()


def test_required_sidecar_requires_private_operator_key_before_binding():
    with (
        patch.object(
            McpAuthenticator,
            "from_env",
            return_value=StubAuthenticator(admin_principal()),
        ),
        patch.object(
            server.Config,
            "sidecar_base_url",
            "http://sidecar.internal",
        ),
        patch.object(server.Config, "sidecar_required", True),
        patch.object(server.Config, "sidecar_api_key", ""),
        patch.object(server, "ThreadingHTTPServer") as listener,
        pytest.raises(SystemExit, match="MEM0_SIDECAR_API_KEY"),
    ):
        server.main()

    listener.assert_not_called()


def test_required_sidecar_preflight_failure_prevents_binding():
    with (
        patch.object(
            McpAuthenticator,
            "from_env",
            return_value=StubAuthenticator(admin_principal()),
        ),
        patch.object(
            server.Config,
            "sidecar_base_url",
            "http://sidecar.internal",
        ),
        patch.object(server.Config, "sidecar_required", True),
        patch.object(server.Config, "sidecar_api_key", "private-operator-key"),
        patch.object(
            server,
            "_send_sidecar_heartbeat",
            side_effect=server.BackendError(401, "secret detail"),
        ),
        patch.object(server, "ThreadingHTTPServer") as listener,
        pytest.raises(
            SystemExit,
            match="required sidecar authentication or readiness check failed",
        ),
    ):
        server.main()

    listener.assert_not_called()
