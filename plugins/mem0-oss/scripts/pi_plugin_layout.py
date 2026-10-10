from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from install_opencode_plugin import js_literal

ENTRY_ANCHOR: Final = "  const config = loadConfig();"


class InstallerError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class Connection:
    url: str
    api_key_env_var: str
    env_file: Path | None


def validate_mcp_url(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized.lower().startswith(("http://", "https://"))
        or "\\" in normalized
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
    ):
        raise InstallerError("--url must be an absolute http(s) MCP URL")
    script = r"""
try {
  const url = new URL(process.argv[1]);
  if (!["http:", "https:"].includes(url.protocol) || !url.hostname
    || url.username || url.password || url.search || url.hash
    || !url.pathname.replace(/\/+$/, "").endsWith("/mcp")) {
    process.exit(1);
  }
  process.stdout.write(url.href.replace(/\/+$/, ""));
} catch {
  process.exit(1);
}
"""
    try:
        result = subprocess.run(
            ["node", "-e", script, normalized],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except FileNotFoundError as error:
        raise InstallerError("Node.js is required to validate --url") from error
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstallerError("Node.js could not validate --url") from error
    if result.returncode != 0:
        raise InstallerError(
            "--url must be a valid OSS MCP URL without credentials, query or fragment, ending in /mcp"
        )
    return result.stdout.rstrip("/")


def validate_source(path: Path) -> Path:
    root = path.expanduser().resolve()
    source = (
        root
        if (root / "src/entry.ts").is_file()
        else root / "integrations/pi-agent-plugin"
    )
    core = source.parent / "agent-plugin-core/typescript/src"
    if not (core / "lifecycle.ts").is_file():
        raise InstallerError(
            "Pi plugin shared core missing; use a Mem0 checkout with pi-agent-plugin 0.3.2 or later"
        )
    entry = source / "src/entry.ts"
    if not (source / "package.json").is_file() or not entry.is_file():
        raise InstallerError(f"not a Mem0 Pi plugin source directory: {path}")
    if ENTRY_ANCHOR not in entry.read_text(encoding="utf-8"):
        raise InstallerError(
            "Pi plugin entry does not contain the expected config initialization"
        )
    return source


def patch_sources(plugin: Path, connection: Connection, *, portable: bool = False) -> None:
    for path in (plugin / "src").rglob("*.ts"):
        content = path.read_text(encoding="utf-8")
        client = os.path.relpath(plugin / "mem0_oss_pi_client.ts", path.parent)
        core = os.path.relpath(plugin / "src/agent-plugin-core", path.parent)
        if not core.startswith("."):
            core = "./" + core
        content = re.sub(r'(["\'])mem0ai\1', lambda _: f'"{client}"', content)
        content = re.sub(
            r'(["\'])(?:\.\./)+agent-plugin-core/typescript/src/',
            lambda match: f"{match[1]}{core}/",
            content,
        )
        path.write_text(content, encoding="utf-8")
    entry = plugin / "src/entry.ts"
    content = entry.read_text(encoding="utf-8")
    url = 'process.env.MEM0_OSS_MCP_URL || ""' if portable else js_literal(connection.url)
    init = (
        "  initializeMem0OssEnv({\n"
        f"    url: {url},\n"
        f"    apiKeyEnvVar: {js_literal(connection.api_key_env_var)},\n"
        f"    envFile: {js_literal(str(connection.env_file) if connection.env_file else None)},\n"
        "  });\n"
    )
    content = (
        'import { initializeMem0OssEnv } from "../mem0_oss_pi_client.ts";\n' + content
    )
    entry.write_text(
        content.replace(ENTRY_ANCHOR, init + ENTRY_ANCHOR, 1), encoding="utf-8"
    )
    config = plugin / "src/config/index.ts"
    content = (
        config.read_text(encoding="utf-8")
        .replace(
            'const AGENT_ROOT = path.join(os.homedir(), ".pi", "agent");',
            'const AGENT_ROOT = process.env.PI_CODING_AGENT_DIR || path.join(os.homedir(), ".pi", "agent");',
        )
        .replace(
            "process.env.MEM0_API_KEY",
            "process.env.MEM0_OSS_PI_RESOLVED_API_KEY",
        )
    )
    config.write_text(content, encoding="utf-8")
    mutations = (
        (
            "memory/tools.ts",
            "mem0.update(memoryId, { text: params.content })",
            "mem0.update(memoryId, { text: params.content, filters: resolveSearchFilters(scope, scopeCtx) })",
        ),
        (
            "memory/tools.ts",
            "mem0.delete(normalizeMemoryId(params.memory_id))",
            "mem0.delete(normalizeMemoryId(params.memory_id), { filters: resolveSearchFilters(scope, scopeCtx) })",
        ),
        (
            "commands.ts",
            "mem0.delete(target.id)",
            "mem0.delete(target.id, { filters: resolveSearchFilters(config.defaultScope, getScopeCtx()) })",
        ),
    )
    for relative, before, after in mutations:
        path = plugin / "src" / relative
        content = path.read_text(encoding="utf-8")
        if before not in content:
            raise InstallerError(f"unsupported Pi mutation site in {relative}")
        path.write_text(content.replace(before, after), encoding="utf-8")
