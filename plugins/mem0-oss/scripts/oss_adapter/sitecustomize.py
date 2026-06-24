"""Route official Mem0 plugin Python hook API calls through Mem0 OSS MCP.

Generated full-experience plugins copy the official Mem0 Codex plugin from the
`third_party/mem0` submodule. Those official scripts call the hosted
`https://api.mem0.ai/v3` API with urllib. Python imports `sitecustomize`
automatically when this directory is on `PYTHONPATH`, so this small adapter
translates those hosted API calls into MCP `tools/call` requests instead.
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ORIGINAL_URLOPEN = urllib.request.urlopen
MEM0_PLATFORM_HOST = "api.mem0.ai"


def parse_dotenv_value(value: str) -> str:
    try:
        parts = shlex.split(value.strip(), comments=True, posix=True)
    except ValueError:
        return ""
    return parts[0] if parts else ""


class JsonResponse:
    status = 200

    def __init__(self, payload: Any):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def read(self, *_args, **_kwargs):
        return self._body

    def getcode(self):
        return self.status

    def info(self):
        return {}


def read_dotenv(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path:
        return values
    env_path = Path(path).expanduser()
    if not env_path.is_file():
        return values
    try:
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = parse_dotenv_value(value)
    return values


def resolve_token() -> str:
    token_env_var = os.environ.get("MEM0_OSS_MCP_TOKEN_ENV_VAR", "MEM0_OSS_MCP_TOKEN")
    dotenv = read_dotenv(os.environ.get("MEM0_OSS_ENV_FILE", ""))

    value = os.environ.get(token_env_var, "").strip()
    if value:
        return value
    value = dotenv.get(token_env_var, "").strip()
    if value:
        return value

    if token_env_var != "MEM0_OSS_MCP_TOKEN":
        value = os.environ.get("MEM0_OSS_MCP_TOKEN", "").strip()
        if value:
            return value
        value = dotenv.get("MEM0_OSS_MCP_TOKEN", "").strip()
        if value:
            return value

    value = dotenv.get("MEM0_API_KEY", "").strip()
    if value:
        return value
    value = os.environ.get("MEM0_API_KEY", "").strip()
    if value:
        return value
    return ""


def call_tool(name: str, arguments: dict[str, Any], timeout: float | None = None) -> Any:
    token = resolve_token()
    if not token:
        raise RuntimeError("Mem0 OSS MCP token is not set")
    url = os.environ.get("MEM0_OSS_MCP_URL", "").strip().rstrip("/")
    if not url:
        raise RuntimeError("MEM0_OSS_MCP_URL is not set")

    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with ORIGINAL_URLOPEN(request, timeout=15 if timeout is None else timeout) as response:
        envelope = json.loads(response.read().decode("utf-8"))

    if "error" in envelope:
        raise RuntimeError(envelope["error"].get("message", str(envelope["error"])))
    result = envelope.get("result", {})
    if result.get("isError"):
        content = result.get("content") or []
        if content and isinstance(content[0], dict):
            raise RuntimeError(content[0].get("text", str(result)))
        raise RuntimeError(str(result))

    content = result.get("content") or []
    if not content:
        return result
    text = content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def request_url(request: Any) -> str:
    return getattr(request, "full_url", request)


def request_method(request: Any) -> str:
    if hasattr(request, "get_method"):
        return request.get_method().upper()
    return "GET"


def request_body(request: Any, data: Any) -> dict[str, Any]:
    raw = data if data is not None else getattr(request, "data", None)
    if raw is None:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not raw:
        return {}
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return body if isinstance(body, dict) else {}


def query_args(parsed: urllib.parse.ParseResult) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, items in urllib.parse.parse_qs(parsed.query).items():
        if not items:
            continue
        value: Any = items[-1]
        if key in {"page", "page_size", "top_k"}:
            try:
                value = int(value)
            except ValueError:
                pass
        values[key] = value
    return values


def dispatch_platform_call(
    parsed: urllib.parse.ParseResult,
    method: str,
    body: dict[str, Any],
    timeout: float | None = None,
) -> Any:
    path = parsed.path.rstrip("/")
    args = {**body, **query_args(parsed)}

    if path == "/v3/memories/add":
        return call_tool("add_memory", args, timeout=timeout)
    if path == "/v3/memories/search":
        return call_tool("search_memories", args, timeout=timeout)
    if path == "/v3/memories":
        if method == "DELETE":
            return call_tool("delete_all_memories", args, timeout=timeout)
        return call_tool("get_memories", args, timeout=timeout)

    if path.startswith("/v3/memories/"):
        memory_id = urllib.parse.unquote(path.rsplit("/", 1)[-1])
        if memory_id in {"events", "event"}:
            return call_tool("get_event_status", args, timeout=timeout)
        args.setdefault("id", memory_id)
        if method == "DELETE":
            return call_tool("delete_memory", args, timeout=timeout)
        if method in {"PATCH", "PUT"}:
            if "memory" in args and "text" not in args:
                args["text"] = args["memory"]
            return call_tool("update_memory", args, timeout=timeout)
        return call_tool("get_memory", args, timeout=timeout)

    if path.startswith("/v1/event/") or "/events/" in path:
        args.setdefault("event_id", urllib.parse.unquote(path.rsplit("/", 1)[-1]))
        return call_tool("get_event_status", args, timeout=timeout)
    if path in {"/v1/events", "/v3/events"}:
        return call_tool("list_events", args, timeout=timeout)

    raise RuntimeError(f"unsupported Mem0 Platform endpoint in OSS adapter: {parsed.geturl()}")


def urlopen(request: Any, data: Any = None, timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT, *args, **kwargs):
    url = request_url(request)
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc != MEM0_PLATFORM_HOST:
        return ORIGINAL_URLOPEN(request, data=data, timeout=timeout, *args, **kwargs)
    try:
        request_timeout = None if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
        payload = dispatch_platform_call(parsed, request_method(request), request_body(request, data), request_timeout)
    except Exception as exc:
        raise urllib.error.URLError(f"mem0 OSS MCP adapter failed: {exc}") from exc
    return JsonResponse(payload)


urllib.request.urlopen = urlopen
