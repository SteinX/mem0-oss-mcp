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
def running_server(authenticator):
    with (
        patch.object(server.Config, "authenticator", authenticator),
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
    with running_server(StubAuthenticator(Unauthorized("credential rejected"))) as base_url:
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


def test_health_does_not_require_client_credentials():
    with running_server(StubAuthenticator(Unauthorized("credential rejected"))) as base_url:
        with urllib.request.urlopen(f"{base_url}/health", timeout=5) as response:
            body = json.loads(response.read())
            status = response.status

    assert status == 200
    assert body["status"] == "ok"


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
