from __future__ import annotations

import os
import re
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


def patch_sources(plugin: Path, connection: Connection) -> None:
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
    init = (
        "  initializeMem0OssEnv({\n"
        f"    url: {js_literal(connection.url)},\n"
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
    content = config.read_text(encoding="utf-8").replace(
        'const AGENT_ROOT = path.join(os.homedir(), ".pi", "agent");',
        'const AGENT_ROOT = process.env.PI_CODING_AGENT_DIR || path.join(os.homedir(), ".pi", "agent");',
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
