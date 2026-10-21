"""Build a native Devin bundle from the upstream portable Mem0 plugin."""

from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path

from codex_plugin_layout import write_native_oss_adapter
from install_codex_plugin import copy_plugin, load_json, write_json

ADAPTER = Path(__file__).resolve().parent / "oss_adapter"


def source_directory(path: Path) -> Path:
    root = path.expanduser().resolve()
    plugin = (
        root
        if (root / "plugin.json").is_file()
        else root / "integrations/mem0-agent-plugin"
    )
    required = (
        "plugin.json",
        "mcp.json",
        "core/memory_core.py",
        "core/memory_cli.py",
        "core/telemetry.py",
        "core/_harness_id.py",
        "skills/search/SKILL.md",
    )
    if not all((plugin / name).is_file() for name in required):
        raise ValueError(f"not a complete Mem0 portable agent plugin: {root}")
    shared = plugin.parent / "agent-plugin-core/python"
    if not all(
        (shared / name).is_file() for name in ("hook_runner.py", "flush_worker.py")
    ):
        raise ValueError("Mem0 shared hook runtime missing; use a full Mem0 checkout")
    return plugin


def replace_once(path: Path, old: str, new: str) -> None:
    content = path.read_text(encoding="utf-8")
    if content.count(old) != 1:
        raise ValueError(
            f"upstream {path.name} contract changed; cannot safely adapt Devin"
        )
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


def generate(
    source: Path, staging: Path, installed: Path, name: str, config: dict
) -> None:
    copy_plugin(source, staging)
    shared = source.parent / "agent-plugin-core/python"
    for filename in ("hook_runner.py", "flush_worker.py"):
        shutil.copy2(shared / filename, staging / "core" / filename)

    # Reuse the checked native OSS transport/event fixes. Devin can also use
    # direct Core, where durable idempotency keys are unsupported.
    write_native_oss_adapter(ADAPTER, staging)
    shutil.copy2(
        ADAPTER / "mem0_oss_devin_bootstrap.py", staging / "core/mem0_oss_bootstrap.py"
    )
    replace_once(
        staging / "core/memory_core.py",
        '"idempotency_key": mem0_oss_bootstrap.stable_batch_key(packet_id, index)',
        "**mem0_oss_bootstrap.batch_options(packet_id, index)",
    )
    replace_once(
        staging / "core/memory_core.py",
        "def api_key() -> str:\n",
        "def api_key() -> str:\n    return mem0_oss_bootstrap.resolve_token()\n\n\n"
        "def _upstream_api_key() -> str:\n",
    )
    replace_once(
        staging / "core/memory_core.py",
        '    """Run remote extraction at a durable boundary."""\n    return flush_session(store, hook_input, reason)',
        '    """Run remote extraction at a durable boundary."""\n'
        "    if store.is_paused() or not mem0_oss_bootstrap.capture_enabled():\n"
        '        return {"status": "paused"}\n'
        "    return flush_session(store, hook_input, reason)",
    )
    replace_once(
        staging / "core/memory_cli.py",
        "from __future__ import annotations\n",
        "from __future__ import annotations\n\nimport mem0_oss_bootstrap\n",
    )
    replace_once(
        staging / "core/memory_cli.py",
        '                "api_key_configured": bool(api_key()),',
        '                "api_key_configured": bool(api_key()),\n'
        '                "auto_capture": mem0_oss_bootstrap.capture_enabled(),\n'
        '                "durable_writes": mem0_oss_bootstrap.CONFIG["durable_writes"],',
    )
    (staging / "hooks").mkdir(exist_ok=True)
    (staging / "scripts").mkdir(exist_ok=True)
    shutil.copy2(ADAPTER / "mem0_oss_devin_hook.py", staging / "hooks/adapter.py")
    for filename in ("mem0_oss_devin_mcp.py", "mem0_oss_stdio_bridge.py"):
        shutil.copy2(ADAPTER / filename, staging / "scripts" / filename)

    manifest = load_json(staging / "plugin.json")
    manifest.pop("$schema", None)
    manifest.update(
        name=name,
        version=str(manifest["version"]).split("+", 1)[0] + "+oss.devin",
        description="Repository memory for Devin CLI through a self-hosted Mem0 OSS MCP bridge.",
        mcpServers="./.mcp.json",
    )
    write_json(staging / ".devin-plugin/plugin.json", manifest)
    (staging / "plugin.json").unlink()
    (staging / "mcp.json").unlink()
    write_json(staging / ".mem0-oss-runtime.json", {"generated_for": "devin", **config})
    write_json(
        staging / ".mcp.json",
        {
            "mcpServers": {
                "mem0": {
                    "command": sys.executable,
                    "args": ["${PLUGIN_ROOT}/scripts/mem0_oss_devin_mcp.py"],
                }
            }
        },
    )

    actions = {
        "SessionStart": ["session-start"],
        "UserPromptSubmit": ["user-prompt"],
        "PostToolUse": ["post-tool"],
        "Stop": ["stop"],
        "PostCompaction": ["flush", "--reason", "post-compaction"],
        "SessionEnd": ["flush", "--reason", "session-end"],
    }
    # Local installations are linked to this path. No undocumented hook-root
    # variable, global config merge, or Claude/Codex hook registration is needed.
    hooks = {
        event: [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": shlex.join(
                            [sys.executable, str(installed / "hooks/adapter.py"), *args]
                        ),
                        "timeout": 5,
                    }
                ]
            }
        ]
        for event, args in actions.items()
    }
    write_json(staging / "hooks.json", {"hooks": hooks})

    cli = shlex.join(
        [sys.executable, str(installed / "core/memory_cli.py"), "--harness", "devin"]
    )
    templates = ADAPTER / "devin"
    shutil.rmtree(staging / "skills")
    shutil.copytree(templates / "skills", staging / "skills")
    shutil.copy2(templates / "AGENTS.md", staging / "AGENTS.md")
    for path in [staging / "AGENTS.md", *(staging / "skills").rglob("*.md")]:
        path.write_text(
            path.read_text(encoding="utf-8").replace("{{CLI}}", cli), encoding="utf-8"
        )
    license_path = source.parents[1] / "LICENSE"
    if license_path.is_file():
        shutil.copy2(license_path, staging / "LICENSE")
    (staging / "README.md").write_text(
        "# Mem0 OSS for Devin CLI\n\n"
        "Generated from Mem0's portable agent plugin and shared Python core. "
        "Use the installed search, remember, status, pause, resume and forget skills.\n\n"
        "Connection credentials and local evidence live outside this package. "
        "Regenerate at the same path to update; restart Devin after changes.\n",
        encoding="utf-8",
    )
