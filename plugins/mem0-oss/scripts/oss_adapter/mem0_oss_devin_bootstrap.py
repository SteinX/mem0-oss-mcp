"""Load the generated Devin plugin's private OSS connection in every worker."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import mem0_oss_adapter

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / ".mem0-oss-runtime.json").read_text(encoding="utf-8"))

# Connection selection belongs to this local plugin, not another installed
# client's environment. Credentials themselves stay outside the package.
os.environ["MEM0_OSS_MCP_URL"] = CONFIG["url"]
os.environ["MEM0_OSS_MCP_TOKEN_ENV_VAR"] = CONFIG["token_env_var"]
os.environ["MEM0_OSS_ENV_FILE"] = CONFIG.get("env_file", "")
os.environ["MEM0_CODE_DATA_DIR"] = CONFIG["data_dir"]
# This virtual URL is intercepted by the shared adapter; network requests use
# CONFIG["url"]. Do not inherit a hosted/custom API URL that bypasses the shim.
os.environ["MEM0_API_URL"] = f"https://{mem0_oss_adapter.MEM0_PLATFORM_HOST}"
os.environ["MEM0_TELEMETRY"] = "false"
os.environ.setdefault("MEM0_CODE_SEARCH_SCOPE", "mine")
os.environ.setdefault("MEM0_CODE_AUTO_FLUSH", str(CONFIG["auto_capture"]).lower())
if CONFIG.get("user_id"):
    os.environ["MEM0_CODE_USER_ID"] = CONFIG["user_id"]


def resolve_token() -> str:
    """Never borrow a hosted key or another client's default credential."""
    name = CONFIG["token_env_var"]
    return (
        os.environ.get(name, "").strip()
        or mem0_oss_adapter.read_dotenv(CONFIG.get("env_file", ""))
        .get(name, "")
        .strip()
    )


mem0_oss_adapter.resolve_token = resolve_token


def capture_enabled() -> bool:
    return os.environ.get("MEM0_CODE_AUTO_FLUSH", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def batch_options(packet_id: str, index: int) -> dict[str, str]:
    if not CONFIG["durable_writes"]:
        return {}
    digest = hashlib.sha256(f"{packet_id}:{index}".encode()).hexdigest()
    return {"idempotency_key": f"devin-{digest}"}
