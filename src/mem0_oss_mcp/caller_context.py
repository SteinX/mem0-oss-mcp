from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal, TypedDict

from .auth import AuthPrincipal, CredentialKind


CALLER_CONTEXT_HEADER = "X-Mem0-Caller-Context"
MAX_CALLER_CONTEXT_BYTES = 2048


class CallerContextPayload(TypedDict):
    v: Literal[1]
    transport: Literal["mcp"]
    credential_kind: CredentialKind
    credential_id: str | None
    label: str | None
    key_prefix: str | None


class CallerContextTooLarge(RuntimeError):
    pass


_CURRENT_PRINCIPAL: ContextVar[AuthPrincipal | None] = ContextVar(
    "mem0_mcp_principal",
    default=None,
)


@contextmanager
def bind_http_principal(principal: AuthPrincipal) -> Iterator[None]:
    token = _CURRENT_PRINCIPAL.set(principal)
    try:
        yield
    finally:
        _CURRENT_PRINCIPAL.reset(token)


def encode_current_caller_context() -> str | None:
    principal = _CURRENT_PRINCIPAL.get()
    if principal is None:
        return None

    payload = CallerContextPayload(
        v=1,
        transport="mcp",
        credential_kind=principal.credential_kind,
        credential_id=principal.credential_id,
        label=principal.credential_label,
        key_prefix=principal.credential_prefix,
    )
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(serialized).rstrip(b"=")
    if len(encoded) > MAX_CALLER_CONTEXT_BYTES:
        raise CallerContextTooLarge("caller context exceeds header limit")
    return encoded.decode("ascii")
