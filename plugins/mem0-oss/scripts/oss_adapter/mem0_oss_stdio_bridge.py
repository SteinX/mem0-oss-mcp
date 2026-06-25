#!/usr/bin/env python3
"""Proxy stdio MCP JSON-RPC messages to a Mem0 OSS HTTP MCP endpoint.

Codex Desktop does not reliably inherit custom shell environment variables.
Generated plugins can run this stdio bridge instead of configuring the HTTP
MCP endpoint directly. The bridge reads the bearer token from MEM0_OSS_ENV_FILE
and forwards each JSON-RPC message to MEM0_OSS_MCP_URL.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_TOKEN_ENV_VAR = "MEM0_OSS_MCP_TOKEN"


def parse_dotenv_value(value: str) -> str:
    try:
        parts = shlex.split(value.strip(), comments=False, posix=True)
    except ValueError:
        return ""
    return parts[0] if parts else ""


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
    token_env_var = os.environ.get("MEM0_OSS_MCP_TOKEN_ENV_VAR", DEFAULT_TOKEN_ENV_VAR)
    dotenv = read_dotenv(os.environ.get("MEM0_OSS_ENV_FILE", ""))

    value = os.environ.get(token_env_var, "").strip()
    if value:
        return value
    value = dotenv.get(token_env_var, "").strip()
    if value:
        return value

    if token_env_var != DEFAULT_TOKEN_ENV_VAR:
        value = os.environ.get(DEFAULT_TOKEN_ENV_VAR, "").strip()
        if value:
            return value
        value = dotenv.get(DEFAULT_TOKEN_ENV_VAR, "").strip()
        if value:
            return value

    value = dotenv.get("MEM0_API_KEY", "").strip()
    if value:
        return value
    value = os.environ.get("MEM0_API_KEY", "").strip()
    if value:
        return value
    return ""


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def response_messages(raw: bytes) -> list[str]:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    if text.startswith("{") or text.startswith("["):
        return [text]

    messages: list[str] = []
    for event in text.split("\n\n"):
        data_lines = []
        for line in event.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            continue
        data = "\n".join(data_lines).strip()
        if data and data != "[DONE]":
            messages.append(data)
    return messages


def proxy_message(message: dict[str, Any], url: str, token: str) -> list[str]:
    payload = json.dumps(message).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/"),
        data=payload,
        method="POST",
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response_messages(response.read())


def emit(message: dict[str, Any] | str) -> None:
    if isinstance(message, str):
        print(message, flush=True)
    else:
        print(json.dumps(message, separators=(",", ":")), flush=True)


def handle_message(message: dict[str, Any], url: str, token: str, output_lock: threading.Lock) -> None:
    request_id = message.get("id")
    if not url:
        if request_id is not None:
            with output_lock:
                emit(error_response(request_id, -32000, "MEM0_OSS_MCP_URL is not set"))
        return
    if not token:
        if request_id is not None:
            with output_lock:
                emit(error_response(request_id, -32001, "Mem0 OSS MCP token is not set"))
        return

    try:
        responses = proxy_message(message, url, token)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        if request_id is not None:
            with output_lock:
                emit(error_response(request_id, -32002, f"Mem0 OSS MCP proxy failed: {exc}"))
        return

    with output_lock:
        for response in responses:
            emit(response)


def main() -> int:
    url = os.environ.get("MEM0_OSS_MCP_URL", "").strip()
    token = resolve_token()
    output_lock = threading.Lock()
    workers: list[threading.Thread] = []

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            print("mem0 stdio bridge received invalid JSON", file=sys.stderr, flush=True)
            continue
        if not isinstance(message, dict):
            print("mem0 stdio bridge received non-object JSON-RPC message", file=sys.stderr, flush=True)
            continue

        worker = threading.Thread(target=handle_message, args=(message, url, token, output_lock))
        worker.start()
        workers.append(worker)

    for worker in workers:
        worker.join()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
