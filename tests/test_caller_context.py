import base64
import json
import threading
from unittest.mock import patch

import pytest

from mem0_oss_mcp import server
from mem0_oss_mcp.auth import AuthPrincipal
from mem0_oss_mcp.caller_context import (
    CALLER_CONTEXT_HEADER,
    bind_http_principal,
    encode_current_caller_context,
)


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return b'{"status":"ok"}'


class DispatchFailure(RuntimeError):
    pass


def _decode_header(value):
    padding = "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(value + padding))


def _core_principal(label):
    return AuthPrincipal(
        mechanism="core_api_key",
        subject="8f7ebdcc-0df0-42e7-8f43-e7db627c9788",
        role="admin",
        credential_kind="core_api_key",
        credential_id="e0544e3c-d217-40d9-bc9a-c1f64077542a",
        credential_label=label,
        credential_prefix="m0sk_client_",
    )


def test_private_sidecar_request_carries_only_bounded_safe_caller_context():
    observed = {}

    def fake_urlopen(request, timeout):
        observed["headers"] = {
            name.lower(): value for name, value in request.header_items()
        }
        return FakeResponse()

    with (
        patch.object(server.Config, "sidecar_base_url", "http://sidecar.internal"),
        patch.object(server.Config, "sidecar_api_key", "internal-sidecar-secret"),
        patch.object(
            server,
            "_open_no_redirect",
            side_effect=fake_urlopen,
        ),
        bind_http_principal(_core_principal("codex-devbox")),
    ):
        server._sidecar_backend("POST", "/v1/memories", {"text": "remember"})

    encoded = observed["headers"][CALLER_CONTEXT_HEADER.lower()]
    assert len(encoded.encode("ascii")) <= 2048
    assert _decode_header(encoded) == {
        "credential_id": "e0544e3c-d217-40d9-bc9a-c1f64077542a",
        "credential_kind": "core_api_key",
        "key_prefix": "m0sk_client_",
        "label": "codex-devbox",
        "transport": "mcp",
        "v": 1,
    }
    assert "internal-sidecar-secret" not in base64.urlsafe_b64decode(
        encoded + "=" * (-len(encoded) % 4)
    ).decode("utf-8")


def test_static_principal_uses_constant_descriptor_without_secret_fingerprint():
    secret = "legacy-secret-that-must-not-leak"
    principal = AuthPrincipal(
        mechanism="static",
        credential_kind="legacy_static",
        credential_label="Legacy shared MCP key",
    )

    with bind_http_principal(principal):
        encoded = encode_current_caller_context()

    payload = _decode_header(encoded)
    assert payload == {
        "credential_id": None,
        "credential_kind": "legacy_static",
        "key_prefix": None,
        "label": "Legacy shared MCP key",
        "transport": "mcp",
        "v": 1,
    }
    assert secret not in json.dumps(payload)


def test_caller_context_is_not_sent_without_trusted_sidecar_credential():
    observed = {}

    def fake_urlopen(request, timeout):
        observed["headers"] = {
            name.lower(): value for name, value in request.header_items()
        }
        return FakeResponse()

    with (
        patch.object(server.Config, "sidecar_base_url", "http://sidecar.internal"),
        patch.object(server.Config, "sidecar_api_key", ""),
        patch.object(
            server,
            "_open_no_redirect",
            side_effect=fake_urlopen,
        ),
        bind_http_principal(_core_principal("codex-devbox")),
    ):
        server._sidecar_backend("GET", "/v1/memories")

    assert CALLER_CONTEXT_HEADER.lower() not in observed["headers"]


def test_system_sidecar_request_never_inherits_prior_http_principal():
    observed = {}

    def fake_urlopen(request, timeout):
        observed["headers"] = {
            name.lower(): value for name, value in request.header_items()
        }
        return FakeResponse()

    with bind_http_principal(_core_principal("codex-devbox")):
        assert encode_current_caller_context() is not None

    with (
        patch.object(server.Config, "sidecar_base_url", "http://sidecar.internal"),
        patch.object(server.Config, "sidecar_api_key", "internal-sidecar-secret"),
        patch.object(
            server,
            "_open_no_redirect",
            side_effect=fake_urlopen,
        ),
    ):
        server._sidecar_backend("GET", "/readyz")

    assert CALLER_CONTEXT_HEADER.lower() not in observed["headers"]


def test_context_resets_after_dispatch_failure():
    with pytest.raises(DispatchFailure, match="rpc failed"):
        with bind_http_principal(_core_principal("codex-devbox")):
            raise DispatchFailure("rpc failed")

    assert encode_current_caller_context() is None


def test_concurrent_requests_keep_distinct_principals():
    barrier = threading.Barrier(2)
    observed = {}

    def capture(label):
        with bind_http_principal(_core_principal(label)):
            barrier.wait(timeout=5)
            observed[label] = _decode_header(encode_current_caller_context())

    threads = [
        threading.Thread(target=capture, args=("codex-devbox",)),
        threading.Thread(target=capture, args=("opencode-devbox",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert observed["codex-devbox"]["label"] == "codex-devbox"
    assert observed["opencode-devbox"]["label"] == "opencode-devbox"
    assert encode_current_caller_context() is None
