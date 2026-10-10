from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest


@pytest.fixture(params=["build", "node"])
def pi_failing_tools(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    commands = tmp_path / "bin"
    commands.mkdir()
    if request.param == "build":
        node = shutil.which("node")
        assert node is not None
        (commands / "node").symlink_to(node)
    pnpm = commands / "pnpm"
    pnpm.write_text("#!/bin/sh\nexit 42\n")
    pnpm.chmod(0o755)
    return commands


@pytest.fixture(
    params=[
        "http://mem0:8000/mcp",
        "http://127.0.0.1:8000/mcp",
        "http://[::1]:8000/mcp",
        "https://mem0.example/mcp",
        "http://mem0_dev:8000/mcp",
        "https://记忆.test/mcp",
    ]
)
def pi_mcp_url(request: pytest.FixtureRequest) -> str:
    value = request.param
    assert isinstance(value, str)
    return value


@pytest.fixture
def pi_upstream(tmp_path: Path) -> Path:
    root = tmp_path / "upstream"
    plugin = root / "integrations/pi-agent-plugin"
    (plugin / "src/config").mkdir(parents=True)
    (plugin / "skills/search").mkdir(parents=True)
    (plugin / "skills/search/SKILL.md").write_text("---\nname: search\n---\n")
    (plugin / "package.json").write_text(
        json.dumps(
            {
                "name": "@mem0/pi-agent-plugin",
                "version": "0.3.2",
                "type": "module",
                "pi": {"extensions": ["./dist/entry.js"], "skills": ["./skills"]},
                "dependencies": {"mem0ai": "^3.0.7"},
            }
        )
    )
    (plugin / "src/entry.ts").write_text("""import { MEMORY_POLICY } from "./prompt.ts";
import MemoryClient from "mem0ai";
import { loadConfig } from "./config/index.ts";
import { shared } from "../../agent-plugin-core/typescript/src/lifecycle.ts";
export default function extension() {
  const config = loadConfig();
  pi.on("before_agent_start", async (event, _ctx) => {
    return { systemPrompt: event.systemPrompt };
  });
  return new MemoryClient({ apiKey: config.apiKey });
}
""")
    (plugin / "src/config/index.ts").write_text("""import * as os from "node:os";
import * as path from "node:path";
const AGENT_ROOT = path.join(os.homedir(), ".pi", "agent");
export function loadConfig() { return {apiKey: process.env.MEM0_API_KEY,
  autoCapture: true,
}; }
""")
    (plugin / "src/memory").mkdir()
    (plugin / "src/memory/tools.ts").write_text(
        """  scope?: Scope;
    const scope = resolveToolScope(params.scope, defaultScope);
        const filters = resolveSearchFilters(scope, scopeCtx);
mem0.add(messages, { ...addParams, customCategories: DEFAULT_CUSTOM_CATEGORIES });
mem0.update(memoryId, { text: params.content });
mem0.delete(normalizeMemoryId(params.memory_id));
mem0.deleteAll(delParams);
      scope: Type.Optional(
Use mem0_memory with action "add" to save important facts, preferences, goals, decisions, or lessons the user shares
"""
    )
    (plugin / "src/memory/scoping.ts").write_text(
        "export function detectAppId(cwd: string): string { return cwd; }"
    )
    (plugin / "src/capture").mkdir()
    (plugin / "src/capture/index.ts").write_text("        ...addParams,")
    (plugin / "src/commands.ts").write_text("mem0.delete(target.id);\n")
    core = root / "integrations/agent-plugin-core/typescript/src"
    core.mkdir(parents=True)
    (core / "lifecycle.ts").write_text("export const shared = true;\n")
    return root
