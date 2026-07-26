from __future__ import annotations

import http.client
import ipaddress
import json
import math
import os
import secrets
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Literal, Protocol, TypeAlias, assert_never


AuthMode: TypeAlias = Literal["disabled", "static", "hybrid", "core_api_key"]
AuthMechanism: TypeAlias = Literal["disabled", "static", "core_api_key"]
CredentialKind: TypeAlias = Literal["disabled", "legacy_static", "core_api_key"]
JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
_MAX_CORE_AUTH_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class Unauthorized(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class AuthUnavailable(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class AuthConfigurationError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class AuthSettings:
    mode: str
    static_token: str
    client_auth_url: str
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class AuthPrincipal:
    mechanism: AuthMechanism
    subject: str | None = None
    role: str | None = None
    credential_kind: CredentialKind = "disabled"
    credential_id: str | None = None
    credential_label: str | None = None
    credential_prefix: str | None = None


class OpenResponse(Protocol):
    def __enter__(self) -> OpenResponse: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def read(self, amount: int = -1) -> bytes: ...


class Opener(Protocol):
    def __call__(
        self,
        request: urllib.request.Request,
        *,
        timeout: float,
    ) -> OpenResponse: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        return None


def _default_opener(
    request: urllib.request.Request,
    *,
    timeout: float,
) -> OpenResponse:
    return urllib.request.build_opener(_NoRedirectHandler()).open(
        request,
        timeout=timeout,
    )


def _parse_mode(raw: str) -> AuthMode:
    match raw:  # noqa: MATCH_OK -- environment strings are an open boundary.
        case "disabled" | "static" | "hybrid" | "core_api_key":
            return raw
        case invalid:
            raise AuthConfigurationError(f"unsupported MCP auth mode: {invalid}")


def _bearer_credential(header: str) -> str:
    scheme, separator, credential = header.partition(" ")
    if not separator or scheme.lower() != "bearer" or not credential.strip():
        raise Unauthorized("bearer credential required")
    return credential.strip()


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _is_loopback_host(raw: str) -> bool:
    host = raw.strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _parse_core_principal(payload: JSONValue) -> AuthPrincipal:
    match payload:  # noqa: MATCH_OK -- JSON is an open boundary.
        case {"id": str(subject), "role": str(role), "credential": credential}:
            pass
        case _:
            raise AuthUnavailable("client authentication service unavailable")

    match credential:  # noqa: MATCH_OK -- JSON is an open boundary.
        case {
            "kind": "core_api_key",
            "id": str(credential_id),
            "label": str(label),
            "key_prefix": str(prefix),
        }:
            pass
        case _:
            raise Unauthorized("credential is not a client API key")

    valid_identity = _valid_uuid(subject) and bool(role) and len(role) <= 64
    valid_credential = (
        _valid_uuid(credential_id)
        and 1 <= len(label) <= 255
        and 1 <= len(prefix) <= 12
    )
    if not valid_identity:
        raise AuthUnavailable("client authentication service unavailable")
    if not valid_credential:
        raise Unauthorized("credential is not a valid client API key")

    return AuthPrincipal(
        mechanism="core_api_key",
        subject=subject,
        role=role,
        credential_kind="core_api_key",
        credential_id=credential_id,
        credential_label=label,
        credential_prefix=prefix,
    )


class McpAuthenticator:
    def __init__(
        self,
        settings: AuthSettings,
        opener: Opener | None = None,
    ) -> None:
        self.mode: AuthMode = _parse_mode(settings.mode)
        if self.mode in {"static", "hybrid"} and not settings.static_token:
            raise AuthConfigurationError(
                f"{self.mode} MCP auth requires MEM0_OSS_MCP_TOKEN"
            )
        if self.mode in {"hybrid", "core_api_key"} and not settings.client_auth_url:
            raise AuthConfigurationError(
                f"{self.mode} MCP auth requires MEM0_OSS_MCP_CLIENT_AUTH_URL"
            )
        if not math.isfinite(settings.timeout_seconds) or settings.timeout_seconds <= 0:
            raise AuthConfigurationError(
                "MEM0_OSS_MCP_CLIENT_AUTH_TIMEOUT_SECONDS must be positive"
            )
        self._static_token = settings.static_token
        self._client_auth_url = settings.client_auth_url
        self._timeout_seconds = settings.timeout_seconds
        self._opener = opener or _default_opener

    @classmethod
    def from_env(
        cls,
        base_url: str,
        env: Mapping[str, str] | None = None,
    ) -> McpAuthenticator:
        values = os.environ if env is None else env
        static_token = values.get("MEM0_OSS_MCP_TOKEN", "").strip()
        configured_mode = values.get("MEM0_OSS_MCP_AUTH_MODE", "").strip()
        if configured_mode:
            mode = configured_mode
        elif static_token:
            mode = "static"
        else:
            raise AuthConfigurationError(
                "MCP auth mode must be explicit when MEM0_OSS_MCP_TOKEN is absent"
            )
        if mode == "disabled":
            host = values.get("MEM0_OSS_MCP_HOST", "0.0.0.0")
            if not _is_loopback_host(host):
                raise AuthConfigurationError(
                    "disabled MCP auth requires a loopback host"
                )
        auth_url = values.get(
            "MEM0_OSS_MCP_CLIENT_AUTH_URL",
            f"{base_url.rstrip('/')}/auth/me" if base_url else "",
        ).strip()
        raw_timeout = values.get(
            "MEM0_OSS_MCP_CLIENT_AUTH_TIMEOUT_SECONDS",
            "5",
        )
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError as exc:
            raise AuthConfigurationError(
                "MEM0_OSS_MCP_CLIENT_AUTH_TIMEOUT_SECONDS must be numeric"
            ) from exc
        return cls(
            AuthSettings(
                mode=mode,
                static_token=static_token,
                client_auth_url=auth_url,
                timeout_seconds=timeout_seconds,
            )
        )

    def authenticate(self, authorization_header: str) -> AuthPrincipal:
        match self.mode:
            case "disabled":
                return AuthPrincipal(
                    mechanism="disabled",
                    credential_kind="disabled",
                )
            case "static" | "hybrid":
                credential = _bearer_credential(authorization_header)
                if secrets.compare_digest(credential, self._static_token):
                    return AuthPrincipal(
                        mechanism="static",
                        credential_kind="legacy_static",
                        credential_label="Legacy shared MCP key",
                    )
                if self.mode == "static":
                    raise Unauthorized("credential rejected")
                return self._authenticate_core_api_key(credential)
            case "core_api_key":
                return self._authenticate_core_api_key(
                    _bearer_credential(authorization_header)
                )
            case unreachable:
                assert_never(unreachable)

    def _authenticate_core_api_key(self, credential: str) -> AuthPrincipal:
        request = urllib.request.Request(
            self._client_auth_url,
            method="GET",
            headers={
                "Accept": "application/json",
                "X-API-Key": credential,
            },
        )
        try:
            with self._opener(
                request,
                timeout=self._timeout_seconds,
            ) as response:
                body = response.read(_MAX_CORE_AUTH_RESPONSE_BYTES + 1)
                if len(body) > _MAX_CORE_AUTH_RESPONSE_BYTES:
                    raise AuthUnavailable(
                        "client authentication service unavailable"
                    )
                payload: JSONValue = json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise Unauthorized("credential rejected") from None
            raise AuthUnavailable(
                "client authentication service unavailable"
            ) from None
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.HTTPException,
            json.JSONDecodeError,
            UnicodeError,
        ):
            raise AuthUnavailable(
                "client authentication service unavailable"
            ) from None
        return _parse_core_principal(payload)
