from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import __version__


JSON = dict[str, Any]


class BackendError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(body)
        self.status = status
        self.body = body


class Config:
    base_url = os.environ.get("MEM0_OSS_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("MEM0_OSS_API_KEY", "")
    host = os.environ.get("MEM0_OSS_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MEM0_OSS_MCP_PORT", "8080"))
    token = os.environ.get("MEM0_OSS_MCP_TOKEN", "")
    timeout = float(os.environ.get("MEM0_OSS_TIMEOUT", "30"))
    default_user_id = os.environ.get("MEM0_OSS_DEFAULT_USER_ID", os.environ.get("USER", "codex"))
    default_app_id = os.environ.get("MEM0_OSS_DEFAULT_APP_ID", "default")
    list_fetch_limit = int(os.environ.get("MEM0_OSS_LIST_FETCH_LIMIT", "1000"))


EVENTS: dict[str, JSON] = {}


def _json_default(value: Any) -> str:
    return str(value)


def _backend(method: str, path: str, body: JSON | None = None, query: JSON | None = None) -> Any:
    if not Config.base_url:
        raise BackendError(500, "MEM0_OSS_BASE_URL is not set")
    if not Config.api_key:
        raise BackendError(500, "MEM0_OSS_API_KEY is not set")

    url = f"{Config.base_url}{path}"
    if query:
        clean = {k: v for k, v in query.items() if v is not None}
        if clean:
            url += "?" + urlencode(clean, doseq=True)

    data = None
    headers = {"X-API-Key": Config.api_key, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=Config.timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        raise BackendError(exc.code, exc.read().decode("utf-8") or exc.reason) from exc
    except URLError as exc:
        raise BackendError(502, str(exc.reason)) from exc


def _first(mapping: JSON, *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _filter_values(filters: Any) -> JSON:
    values: JSON = {}

    def walk(obj: Any) -> None:
        if isinstance(obj, list):
            for item in obj:
                walk(item)
            return
        if not isinstance(obj, dict):
            return
        for key, value in obj.items():
            if key in {"AND", "OR"}:
                walk(value)
            elif key == "metadata" and isinstance(value, dict):
                for meta_key, meta_value in value.items():
                    values.setdefault(meta_key, meta_value)
            elif key in {"user_id", "agent_id", "run_id", "app_id", "type"}:
                values.setdefault(key, value)
            elif isinstance(value, dict) and "eq" in value:
                values.setdefault(key, value["eq"])

    walk(filters)
    return values


def normalize_filters(filters: Any) -> Any:
    """Flatten platform-style filters to OSS payload filters where possible."""
    if isinstance(filters, list):
        return [normalize_filters(item) for item in filters]
    if not isinstance(filters, dict):
        return filters

    keys = set(filters)
    if keys == {"AND"} and isinstance(filters.get("AND"), list):
        normalized_items = [normalize_filters(item) for item in filters["AND"]]
        merged = _merge_flat_filters(normalized_items)
        return merged if merged is not None else {"AND": normalized_items}

    if keys == {"OR"} and isinstance(filters.get("OR"), list) and len(filters["OR"]) == 1:
        normalized_item = normalize_filters(filters["OR"][0])
        if isinstance(normalized_item, dict):
            return normalized_item
        return {"OR": [normalized_item]}

    out: JSON = {}
    for key, value in filters.items():
        if key in {"AND", "OR"}:
            out[key] = normalize_filters(value)
        elif key == "metadata" and isinstance(value, dict):
            out.update(value)
        elif isinstance(value, dict) and set(value) == {"eq"}:
            out[key] = value["eq"]
        else:
            out[key] = normalize_filters(value)
    return out


def _merge_flat_filters(items: list[Any]) -> JSON | None:
    merged: JSON = {}
    for item in items:
        if not isinstance(item, dict) or any(key in item for key in ("AND", "OR", "NOT")):
            return None
        for key, value in item.items():
            if key in merged and merged[key] != value:
                return None
            merged[key] = value
    return merged


def _memory_metadata(memory: JSON) -> JSON:
    metadata = memory.get("metadata") or memory.get("metadata_") or {}
    return metadata if isinstance(metadata, dict) else {}


def _memory_app_id(memory: JSON) -> Any:
    metadata = _memory_metadata(memory)
    return metadata.get("app_id") or memory.get("app_id")


def _expiration_value(memory: JSON) -> Any:
    if not isinstance(memory, dict):
        return None
    return _memory_metadata(memory).get("expiration_date") or memory.get("expiration_date")


def _is_expired(memory: JSON) -> bool:
    value = _expiration_value(memory)
    if not value:
        return False
    if isinstance(value, datetime):
        expires_at = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return expires_at <= datetime.now(timezone.utc)
    if isinstance(value, date):
        return value < date.today()
    if not isinstance(value, str):
        return False
    raw = value.strip()
    if not raw:
        return False
    try:
        if len(raw) <= 10:
            return date.fromisoformat(raw[:10]) < date.today()
        expires_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def _without_expired(result: Any) -> Any:
    if isinstance(result, list):
        return [memory for memory in result if not _is_expired(memory)]
    if isinstance(result, dict) and isinstance(result.get("results"), list):
        filtered = [memory for memory in result["results"] if not _is_expired(memory)]
        out = dict(result)
        out["results"] = filtered
        if "count" in out:
            out["count"] = len(filtered)
        return out
    return result


def _search_fetch_limit(requested: int) -> int:
    if requested <= 0:
        return requested
    return max(requested * 3, requested + 10)


def _limit_result_count(result: Any, limit: int | None) -> Any:
    if limit is None:
        return result
    if isinstance(result, list):
        return result[:limit]
    if isinstance(result, dict) and isinstance(result.get("results"), list):
        trimmed = result["results"][:limit]
        out = dict(result)
        out["results"] = trimmed
        if "count" in out:
            out["count"] = len(trimmed)
        return out
    return result


def _matches(memory: JSON, values: JSON) -> bool:
    for key, expected in values.items():
        if key == "app_id":
            actual = _memory_app_id(memory)
        elif key == "type":
            actual = _memory_metadata(memory).get("type")
        else:
            actual = memory.get(key)
        if isinstance(expected, dict) and "in" in expected:
            if actual not in expected["in"]:
                return False
        elif expected not in (None, "*") and actual != expected:
            return False
    return True


def _paged(items: list[Any], args: JSON) -> JSON:
    page = int(args.get("page") or 1)
    size = int(args.get("page_size") or args.get("pageSize") or len(items) or 100)
    start = max(page - 1, 0) * size
    return {"results": items[start : start + size], "count": len(items), "page": page, "page_size": size}


def add_memory(args: JSON) -> JSON:
    text = args.get("text") or args.get("content")
    messages = args.get("messages")
    if not messages:
        if not text:
            raise ValueError("add_memory requires text or messages")
        messages = [{"role": "user", "content": text}]

    metadata = dict(args.get("metadata") or {})
    app_id = args.get("app_id") or metadata.get("app_id") or Config.default_app_id
    metadata.setdefault("app_id", app_id)
    expiration_date = args.get("expiration_date")
    if expiration_date is not None:
        metadata.setdefault("expiration_date", expiration_date)

    body: JSON = {
        "messages": messages,
        "metadata": metadata,
        "user_id": args.get("user_id") or Config.default_user_id,
    }
    if expiration_date is not None:
        body["expiration_date"] = expiration_date
    for key in ("agent_id", "run_id", "infer", "memory_type", "prompt"):
        if key in args and args[key] is not None:
            body[key] = args[key]

    result = _backend("POST", "/memories", body)
    event_id = str(uuid.uuid4())
    event = {
        "event_id": event_id,
        "status": "SUCCEEDED",
        "result": result,
        "memory_id": _extract_memory_id(result),
        "created_at": time.time(),
    }
    EVENTS[event_id] = event
    return {"event_id": event_id, "status": "SUCCEEDED"}


def _extract_memory_id(result: Any) -> str | None:
    if isinstance(result, dict):
        if result.get("id"):
            return str(result["id"])
        rows = result.get("results")
        if isinstance(rows, list) and rows:
            first = rows[0]
            if isinstance(first, dict):
                return str(first.get("id") or "") or None
    return None


def search_memories(args: JSON) -> Any:
    query = args.get("query")
    if not query:
        raise ValueError("search_memories requires query")

    body: JSON = {"query": query, "filters": normalize_filters(args.get("filters") or {})}
    requested_top_k: int | None = None
    top_k = _first(args, "top_k", "topK", "limit")
    if top_k is not None:
        requested_top_k = int(top_k)
        body["top_k"] = _search_fetch_limit(requested_top_k)
    for key in ("threshold", "explain"):
        if key in args and args[key] is not None:
            body[key] = args[key]

    for key in ("user_id", "agent_id", "run_id", "app_id"):
        if args.get(key) is not None:
            body["filters"].setdefault(key, args[key])

    result = _without_expired(_backend("POST", "/search", body))
    return _limit_result_count(result, requested_top_k)


def get_memories(args: JSON) -> JSON:
    include_expired = bool(args.get("include_expired"))
    filters = args.get("filters") or {}
    values = _filter_values(filters)
    for key in ("user_id", "agent_id", "run_id", "app_id"):
        if args.get(key) is not None:
            values[key] = args[key]

    query = {k: values.get(k) for k in ("user_id", "agent_id", "run_id")}
    if Config.list_fetch_limit > 0:
        query["top_k"] = Config.list_fetch_limit
    if include_expired:
        query["show_expired"] = True
    result = _backend("GET", "/memories", query=query)
    items = result.get("results", result if isinstance(result, list) else [])
    if not isinstance(items, list):
        items = []
    filtered = [m for m in items if (include_expired or not _is_expired(m)) and _matches(m, values)]
    return _paged(filtered, args)


def get_memory(args: JSON) -> Any:
    memory_id = args.get("id") or args.get("memory_id")
    if not memory_id:
        raise ValueError("get_memory requires id")
    return _backend("GET", f"/memories/{memory_id}")


def update_memory(args: JSON) -> Any:
    memory_id = args.get("id") or args.get("memory_id")
    text = args.get("text") or args.get("memory_content")
    if not memory_id or text is None:
        raise ValueError("update_memory requires id and text")
    return _backend("PUT", f"/memories/{memory_id}", {"text": text, "metadata": args.get("metadata")})


def delete_memory(args: JSON) -> Any:
    memory_id = args.get("id") or args.get("memory_id")
    if not memory_id:
        raise ValueError("delete_memory requires id")
    return _backend("DELETE", f"/memories/{memory_id}")


def delete_all_memories(args: JSON) -> Any:
    app_id = args.get("app_id")
    if app_id:
        user_id = args.get("user_id") or Config.default_user_id
        memories = get_memories({"user_id": user_id, "app_id": app_id, "page_size": 1000, "include_expired": True})[
            "results"
        ]
        deleted = []
        for memory in memories:
            if memory.get("id"):
                delete_memory({"id": memory["id"]})
                deleted.append(memory["id"])
        return {"message": f"Deleted {len(deleted)} memories", "deleted_ids": deleted}

    query = {k: args.get(k) for k in ("user_id", "agent_id", "run_id") if args.get(k)}
    if not query:
        raise ValueError("delete_all_memories requires user_id, agent_id, run_id, or app_id")
    return _backend("DELETE", "/memories", query=query)


def list_entities(args: JSON) -> Any:
    return _backend("GET", "/entities")


def delete_entities(args: JSON) -> Any:
    pairs = (("user", args.get("user_id")), ("agent", args.get("agent_id")), ("run", args.get("run_id")))
    deleted = []
    for entity_type, entity_id in pairs:
        if entity_id:
            deleted.append(_backend("DELETE", f"/entities/{entity_type}/{entity_id}"))
    if args.get("app_id"):
        raise ValueError("delete_entities(app_id) is not supported by mem0 OSS server; use delete_all_memories with user_id and app_id")
    return {"results": deleted}


def list_events(args: JSON) -> JSON:
    events = sorted(EVENTS.values(), key=lambda e: e["created_at"], reverse=True)
    return _paged(events, args)


def get_event_status(args: JSON) -> JSON:
    event_id = args.get("event_id")
    if not event_id:
        raise ValueError("get_event_status requires event_id")
    if event_id not in EVENTS:
        raise ValueError(f"event not found: {event_id}")
    return EVENTS[event_id]


TOOLS = {
    "add_memory": add_memory,
    "search_memories": search_memories,
    "get_memories": get_memories,
    "get_memory": get_memory,
    "update_memory": update_memory,
    "delete_memory": delete_memory,
    "delete_all_memories": delete_all_memories,
    "delete_entities": delete_entities,
    "list_entities": list_entities,
    "list_events": list_events,
    "get_event_status": get_event_status,
}


def tool_schema() -> list[JSON]:
    def schema(properties: JSON, required: list[str] | None = None) -> JSON:
        return {"type": "object", "properties": properties, "required": required or []}

    common = {
        "user_id": {"type": "string"},
        "agent_id": {"type": "string"},
        "run_id": {"type": "string"},
        "app_id": {"type": "string"},
        "filters": {"type": "object"},
        "metadata": {"type": "object"},
    }
    return [
        {"name": "add_memory", "description": "Save text or conversation history.", "inputSchema": schema({"text": {"type": "string"}, "messages": {"type": "array"}, "infer": {"type": "boolean"}, "expiration_date": {"type": "string"}, **common})},
        {"name": "search_memories", "description": "Semantic search across memories.", "inputSchema": schema({"query": {"type": "string"}, "top_k": {"type": "integer"}, "threshold": {"type": "number"}, **common}, ["query"])},
        {"name": "get_memories", "description": "List memories with filters and pagination.", "inputSchema": schema({"page": {"type": "integer"}, "page_size": {"type": "integer"}, **common})},
        {"name": "get_memory", "description": "Retrieve one memory by ID.", "inputSchema": schema({"id": {"type": "string"}}, ["id"])},
        {"name": "update_memory", "description": "Update memory text or metadata.", "inputSchema": schema({"id": {"type": "string"}, "text": {"type": "string"}, "metadata": {"type": "object"}}, ["id"])},
        {"name": "delete_memory", "description": "Delete one memory by ID.", "inputSchema": schema({"id": {"type": "string"}}, ["id"])},
        {"name": "delete_all_memories", "description": "Delete memories in a specific scope.", "inputSchema": schema(common)},
        {"name": "delete_entities", "description": "Delete user, agent, or run entities.", "inputSchema": schema(common)},
        {"name": "list_entities", "description": "List user, agent, and run entities.", "inputSchema": schema({})},
        {"name": "list_events", "description": "List local bridge memory events.", "inputSchema": schema({"page": {"type": "integer"}, "page_size": {"type": "integer"}})},
        {"name": "get_event_status", "description": "Check local async event status.", "inputSchema": schema({"event_id": {"type": "string"}}, ["event_id"])},
    ]


def handle_rpc(message: JSON) -> JSON | None:
    msg_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mem0-oss-mcp", "version": __version__},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tool_schema()}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in TOOLS:
            return _rpc_error(msg_id, -32602, f"unknown tool: {name}")
        try:
            result = TOOLS[name](args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, default=_json_default)}]},
            }
        except BackendError as exc:
            return _rpc_tool_error(msg_id, f"backend error {exc.status}: {exc.body}")
        except Exception as exc:
            return _rpc_tool_error(msg_id, str(exc))
    if method and method.startswith("notifications/"):
        return None
    return _rpc_error(msg_id, -32601, f"method not found: {method}")


def _rpc_error(msg_id: Any, code: int, message: str) -> JSON:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _rpc_tool_error(msg_id: Any, message: str) -> JSON:
    return {"jsonrpc": "2.0", "id": msg_id, "result": {"isError": True, "content": [{"type": "text", "text": message}]}}


class Handler(BaseHTTPRequestHandler):
    server_version = "mem0-oss-mcp"

    def do_GET(self) -> None:
        if self.path == "/health":
            try:
                _backend("GET", "/configure")
                self._send_json({"status": "ok"})
            except Exception as exc:
                self._send_json({"status": "error", "error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/mcp":
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        if not self._authorized():
            self._send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if isinstance(payload, list):
                responses = [r for item in payload if (r := handle_rpc(item)) is not None]
                self._send_json(responses)
            else:
                response = handle_rpc(payload)
                if response is None:
                    self.send_response(HTTPStatus.ACCEPTED)
                    self.end_headers()
                else:
                    self._send_json(response)
        except json.JSONDecodeError:
            self._send_json(_rpc_error(None, -32700, "parse error"), HTTPStatus.BAD_REQUEST)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", file=sys.stderr)

    def _authorized(self) -> bool:
        if not Config.token:
            return True
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {Config.token}"

    def _send_json(self, body: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(body, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    httpd = ThreadingHTTPServer((Config.host, Config.port), Handler)
    print(f"mem0-oss-mcp listening on {Config.host}:{Config.port}", file=sys.stderr)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
