from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path

import mem0_oss_adapter


manifest = Path(__file__).resolve().parent.parent / ".mcp.json"
configuration = json.loads(manifest.read_text(encoding="utf-8"))
for server in configuration["mcpServers"].values():
    environment = server.get("env", {})
    if server.get("url"):
        environment = {**environment, "MEM0_OSS_MCP_URL": server["url"],
                       "MEM0_OSS_MCP_TOKEN_ENV_VAR": server.get("bearer_token_env_var", "MEM0_OSS_MCP_TOKEN")}
    if "MEM0_OSS_MCP_URL" in environment:
        for name in ("MEM0_OSS_MCP_URL", "MEM0_OSS_MCP_TOKEN_ENV_VAR", "MEM0_OSS_ENV_FILE", "MEM0_CODE_DATA_DIR"):
            if name in environment:
                os.environ.setdefault(name, environment[name])
        break

token = mem0_oss_adapter.resolve_token()
if token:
    os.environ["MEM0_API_KEY"] = token
os.environ["MEM0_API_URL"] = "https://api.mem0.ai"
os.environ["MEM0_TELEMETRY"] = "false"
os.environ.setdefault("MEM0_CODE_SEARCH_SCOPE", "mine")


def stable_batch_key(packet_id: str, index: int) -> str:
    return "codex-" + hashlib.sha256(f"{packet_id}:{index}".encode()).hexdigest()
