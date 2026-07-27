import io
import json
import threading
import urllib.error
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mem0_oss_mcp.auth import (
    AuthConfigurationError,
    AuthSettings,
    AuthUnavailable,
    McpAuthenticator,
    Unauthorized,
)


class FakeResponse:
    def __init__(self, payload, *, raw=False):
        self.payload = payload if raw else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, amount=-1):
        return self.payload if amount < 0 else self.payload[:amount]


def _core_response(
    *,
    label: str = "codex-devbox",
    key_id: str = "e0544e3c-d217-40d9-bc9a-c1f64077542a",
    key_prefix: str = "m0sk_client_",
):
    return {
        "id": "8f7ebdcc-0df0-42e7-8f43-e7db627c9788",
        "name": "Root",
        "email": "root@example.invalid",
        "role": "admin",
        "created_at": "2026-07-26T00:00:00Z",
        "credential": {
            "kind": "core_api_key",
            "id": key_id,
            "label": label,
            "key_prefix": key_prefix,
        },
    }


def test_from_env_requires_explicit_mode_when_legacy_token_is_absent():
    with pytest.raises(AuthConfigurationError, match="explicit"):
        McpAuthenticator.from_env("http://mem0:8000", {})


def test_from_env_derives_static_mode_for_legacy_token():
    static = McpAuthenticator.from_env(
        "http://mem0:8000",
        {"MEM0_OSS_MCP_TOKEN": "legacy-secret"},
    )

    assert static.mode == "static"


def test_disabled_mode_requires_loopback():
    disabled = McpAuthenticator.from_env(
        "http://mem0:8000",
        {
            "MEM0_OSS_MCP_AUTH_MODE": "disabled",
            "MEM0_OSS_MCP_HOST": "127.0.0.1",
        },
    )
    assert disabled.mode == "disabled"

    with pytest.raises(AuthConfigurationError, match="loopback"):
        McpAuthenticator.from_env(
            "http://mem0:8000",
            {
                "MEM0_OSS_MCP_AUTH_MODE": "disabled",
                "MEM0_OSS_MCP_HOST": "0.0.0.0",
            },
        )


def test_static_mode_accepts_only_the_legacy_token():
    authenticator = McpAuthenticator(
        AuthSettings(
            mode="static",
            static_token="legacy-secret",
            client_auth_url="http://mem0:8000/auth/me",
            timeout_seconds=5,
        ),
    )

    principal = authenticator.authenticate("Bearer legacy-secret")

    assert principal.mechanism == "static"
    assert principal.credential_kind == "legacy_static"
    assert principal.credential_id is None
    assert principal.credential_label == "Legacy shared MCP key"
    assert principal.credential_prefix is None
    with pytest.raises(Unauthorized):
        authenticator.authenticate("Bearer wrong")


def test_core_api_key_mode_translates_bearer_to_private_x_api_key():
    observed = {}

    def opener(request, timeout):
        observed["url"] = request.full_url
        observed["x_api_key"] = request.get_header("X-api-key")
        observed["authorization"] = request.get_header("Authorization")
        observed["timeout"] = timeout
        return FakeResponse(_core_response())

    authenticator = McpAuthenticator(
        AuthSettings(
            mode="core_api_key",
            static_token="",
            client_auth_url="http://mem0:8000/auth/me",
            timeout_seconds=3,
        ),
        opener=opener,
    )

    principal = authenticator.authenticate("Bearer m0sk_client_secret")

    assert observed == {
        "url": "http://mem0:8000/auth/me",
        "x_api_key": "m0sk_client_secret",
        "authorization": None,
        "timeout": 3,
    }
    assert principal.mechanism == "core_api_key"
    assert principal.subject == "8f7ebdcc-0df0-42e7-8f43-e7db627c9788"
    assert principal.role == "admin"
    assert principal.credential_id == "e0544e3c-d217-40d9-bc9a-c1f64077542a"
    assert principal.credential_label == "codex-devbox"
    assert principal.credential_prefix == "m0sk_client_"


def test_legacy_empty_label_uses_stable_prefix_fallback():
    authenticator = McpAuthenticator(
        AuthSettings(
            mode="core_api_key",
            static_token="",
            client_auth_url="http://mem0:8000/auth/me",
            timeout_seconds=5,
        ),
        opener=lambda request, timeout: FakeResponse(_core_response(label="")),
    )

    principal = authenticator.authenticate("Bearer m0sk_client_secret")

    assert principal.credential_label == "Legacy client key (m0sk_client_...)"


def test_non_admin_core_key_is_rejected():
    response = _core_response()
    response["role"] = "member"
    authenticator = McpAuthenticator(
        AuthSettings(
            mode="core_api_key",
            static_token="",
            client_auth_url="http://mem0:8000/auth/me",
            timeout_seconds=5,
        ),
        opener=lambda request, timeout: FakeResponse(response),
    )

    with pytest.raises(
        Unauthorized,
        match="administrator credential required",
    ):
        authenticator.authenticate("Bearer m0sk_member_secret")


def test_default_core_auth_opener_never_follows_redirects():
    observed = {"redirect_target_requests": 0}
    redirect_target_url = ""

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/auth/me":
                self.send_response(302)
                self.send_header("Location", redirect_target_url)
                self.end_headers()
                return
            observed["redirect_target_requests"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(_core_response()).encode("utf-8"))

        def log_message(self, format, *args):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    redirect_target_url = f"http://127.0.0.1:{httpd.server_port}/capture"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        authenticator = McpAuthenticator(
            AuthSettings(
                mode="core_api_key",
                static_token="",
                client_auth_url=f"http://127.0.0.1:{httpd.server_port}/auth/me",
                timeout_seconds=5,
            ),
        )

        with pytest.raises(AuthUnavailable):
            authenticator.authenticate("Bearer m0sk_redirect_secret")
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()

    assert observed["redirect_target_requests"] == 0


def test_hybrid_accepts_legacy_without_calling_core_and_falls_back_for_new_key():
    calls = []

    def opener(request, timeout):
        calls.append(request.get_header("X-api-key"))
        return FakeResponse(_core_response(label="opencode-devbox"))

    authenticator = McpAuthenticator(
        AuthSettings(
            mode="hybrid",
            static_token="legacy-secret",
            client_auth_url="http://mem0:8000/auth/me",
            timeout_seconds=5,
        ),
        opener=opener,
    )

    assert authenticator.authenticate("Bearer legacy-secret").mechanism == "static"
    assert calls == []
    assert (
        authenticator.authenticate("Bearer m0sk_new_client").mechanism == "core_api_key"
    )
    assert calls == ["m0sk_new_client"]


@pytest.mark.parametrize("status", [401, 403])
def test_core_rejection_is_unauthorized(status):
    def opener(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            status,
            "rejected",
            Message(),
            io.BytesIO(b'{"detail":"rejected"}'),
        )

    authenticator = McpAuthenticator(
        AuthSettings(
            mode="core_api_key",
            static_token="",
            client_auth_url="http://mem0:8000/auth/me",
            timeout_seconds=5,
        ),
        opener=opener,
    )

    with pytest.raises(Unauthorized, match="credential rejected"):
        authenticator.authenticate("Bearer m0sk_revoked")


@pytest.mark.parametrize(
    "failure",
    ["http_500", "network", "connection_reset", "bad_json", "oversized"],
)
def test_core_failure_is_sanitized_unavailable(failure):
    secret = "m0sk_must_not_leak"

    def opener(request, timeout):
        if failure == "http_500":
            raise urllib.error.HTTPError(
                request.full_url,
                500,
                "boom",
                Message(),
                io.BytesIO(secret.encode("utf-8")),
            )
        if failure == "network":
            raise urllib.error.URLError(f"failed while handling {secret}")
        if failure == "connection_reset":
            raise ConnectionResetError(f"reset while handling {secret}")
        if failure == "oversized":
            return FakeResponse({"padding": "x" * 70_000})
        if failure == "bad_json":
            return FakeResponse(b"{not-json", raw=True)
        return FakeResponse({"not": "a principal"})

    authenticator = McpAuthenticator(
        AuthSettings(
            mode="core_api_key",
            static_token="",
            client_auth_url="http://mem0:8000/auth/me",
            timeout_seconds=5,
        ),
        opener=opener,
    )

    with pytest.raises(AuthUnavailable) as captured:
        authenticator.authenticate(f"Bearer {secret}")
    assert secret not in str(captured.value)


@pytest.mark.parametrize(
    ("response", "expected_error"),
    [
        (_core_response(key_id="not-a-uuid"), Unauthorized),
        (_core_response(label="x" * 256), Unauthorized),
        (_core_response(key_prefix="x" * 13), Unauthorized),
        (
            {
                **_core_response(),
                "credential": {
                    "kind": "operator_static",
                    "id": None,
                    "label": "Legacy admin API key",
                    "key_prefix": None,
                },
            },
            Unauthorized,
        ),
    ],
)
def test_core_descriptor_must_be_a_bounded_exact_client_key(response, expected_error):
    authenticator = McpAuthenticator(
        AuthSettings(
            mode="core_api_key",
            static_token="",
            client_auth_url="http://mem0:8000/auth/me",
            timeout_seconds=5,
        ),
        opener=lambda request, timeout: FakeResponse(response),
    )

    with pytest.raises(expected_error):
        authenticator.authenticate("Bearer m0sk_client_secret")


@pytest.mark.parametrize(
    ("mode", "token"),
    [
        ("unknown", "legacy-secret"),
        ("static", ""),
        ("hybrid", ""),
    ],
)
def test_invalid_configuration_fails_at_construction(mode, token):
    with pytest.raises(AuthConfigurationError):
        McpAuthenticator(
            AuthSettings(
                mode=mode,
                static_token=token,
                client_auth_url="http://mem0:8000/auth/me",
                timeout_seconds=5,
            ),
        )
