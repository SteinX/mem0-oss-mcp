# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: imported by the portable Codex runtime and release installer.
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Final, TypedDict
from urllib.parse import urlsplit

from release_json import ReleaseError, json_object, json_text, read_json


class ConnectionUrl(TypedDict):
    url: str


class Connection(ConnectionUrl, total=False):
    env_file: str
    token_env_var: str
    user_id: str
    app_id: str


FIELDS: Final = {
    "url": "MEM0_OSS_MCP_URL",
    "env_file": "MEM0_OSS_ENV_FILE",
    "token_env_var": "MEM0_OSS_MCP_TOKEN_ENV_VAR",
    "user_id": "MEM0_USER_ID",
    "app_id": "MEM0_APP_ID",
}


def validate_endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and re.match(r"^https?://", value, re.IGNORECASE)
            and parsed.path.rstrip("/").endswith("/mcp")
            and not re.search(r"[\\\x00-\x20\x7f]", value)
            and not (
                parsed.username or parsed.password or parsed.query or parsed.fragment
            )
        )
    except ValueError:
        valid = False
    if not valid:
        raise ReleaseError(
            "expected an HTTP(S) MCP endpoint without credentials, query or fragment"
        )
    return value.rstrip("/")


def validate_token_env_var(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ReleaseError("invalid token environment variable name")
    return value


def read_connection(path: Path) -> Connection:
    try:
        value = read_json(path)
    except (OSError, ValueError) as error:
        raise ReleaseError("cannot read external connection configuration") from error
    if any(key not in FIELDS for key in value):
        raise ReleaseError("invalid external connection configuration")
    connection: Connection = {"url": validate_endpoint(json_text(value.get("url")))}
    if "env_file" in value:
        env_file = Path(json_text(value["env_file"])).expanduser()
        connection["env_file"] = str((path.parent / env_file).resolve())
    if "token_env_var" in value:
        connection["token_env_var"] = validate_token_env_var(
            json_text(value["token_env_var"])
        )
    if "user_id" in value:
        connection["user_id"] = json_text(value["user_id"])
    if "app_id" in value:
        connection["app_id"] = json_text(value["app_id"])
    return connection


def endpoint_origin(value: str) -> tuple[str, str | None, int]:
    parsed = urlsplit(validate_endpoint(value))
    return (
        parsed.scheme,
        parsed.hostname,
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


def load_codex_environment() -> None:
    location = os.environ.get("MEM0_OSS_CLIENT_CONFIG")
    manifest = Path(__file__).resolve().parent.parent / ".mcp.json"
    if not location and manifest.is_file():
        servers = json_object(read_json(manifest).get("mcpServers"))
        server = json_object(servers.get("mem0"))
        environment = json_object(server.get("env", {}))
        if "MEM0_OSS_CLIENT_CONFIG" in environment:
            location = json_text(environment["MEM0_OSS_CLIENT_CONFIG"])
    path = (
        Path(location).expanduser()
        if location
        else Path.home() / ".mem0-oss/clients/codex/connection.json"
    )
    if path.is_file():
        connection = read_connection(path)
        url = os.environ.get("MEM0_OSS_MCP_URL") or connection["url"]
        token_name = validate_token_env_var(
            os.environ.get("MEM0_OSS_MCP_TOKEN_ENV_VAR")
            or connection.get("token_env_var", "MEM0_OSS_MCP_TOKEN")
        )
        token = next(
            (
                os.environ[name].strip()
                for name in (token_name, "MEM0_OSS_MCP_TOKEN", "MEM0_API_KEY")
                if os.environ.get(name, "").strip()
            ),
            None,
        )
        if not token and endpoint_origin(url) != endpoint_origin(connection["url"]):
            raise ReleaseError(
                "a different endpoint origin requires an explicit environment token"
            )
        os.environ["MEM0_OSS_MCP_URL"] = validate_endpoint(url)
        os.environ["MEM0_OSS_MCP_TOKEN_ENV_VAR"] = token_name
        if token:
            os.environ[token_name] = token
        values = (
            ("MEM0_OSS_ENV_FILE", connection.get("env_file")),
            ("MEM0_USER_ID", connection.get("user_id")),
            ("MEM0_APP_ID", connection.get("app_id")),
        )
        for name, value in values:
            if value is not None:
                _ = os.environ.setdefault(name, value)
    _ = os.environ.setdefault("MEM0_CODE_DATA_DIR", str(path.parent / "data"))
    _ = os.environ.setdefault("MEM0_TELEMETRY", "false")


if __name__ == "__main__":
    load_codex_environment()
