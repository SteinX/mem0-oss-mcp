# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: imported by install_client_release.py.
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Literal

from release_json import (
    ReleaseError,
    assert_never,
    json_list,
    json_object,
    json_text,
    read_json,
)

Client = Literal["codex", "opencode"]


def host_files(client: Client, host: Path) -> list[Path]:
    match client:
        case "codex":
            return [host / "config.toml"]
        case "opencode":
            return [host / "plugins/mem0-oss.js"]
        case unreachable:
            assert_never(unreachable)


def bind_codex(package: Path, config: Path, runtime_root: Path | None = None) -> None:
    mcp = package / ".mcp.json"
    value = read_json(mcp)
    servers = json_object(value.get("mcpServers"))
    server = json_object(servers.get("mem0"))
    server["args"] = [str((runtime_root or package) / "scripts/release_mcp.py")]
    server["env"] = {"MEM0_OSS_CLIENT_CONFIG": str(config)}
    _ = mcp.write_text(json.dumps(value, indent=2) + "\n")
    hooks = package / "hooks/hooks.json"
    value = read_json(hooks)
    for entries in json_object(value.get("hooks")).values():
        for entry in json_list(entries):
            for raw_hook in json_list(json_object(entry).get("hooks")):
                hook = json_object(raw_hook)
                hook["command"] = (
                    "MEM0_OSS_CLIENT_CONFIG="
                    + shlex.quote(str(config))
                    + " "
                    + json_text(hook.get("command")).replace(
                        '"${PLUGIN_DATA}"', shlex.quote(str(config.parent / "data"))
                    )
                )
    _ = hooks.write_text(json.dumps(value, indent=2) + "\n")
    for skill in (package / "skills").rglob("SKILL.md"):
        _ = skill.write_text(
            skill.read_text().replace(
                '"${PLUGIN_DATA}"', shlex.quote(str(config.parent / "data"))
            )
        )


def enable_hooks(path: Path) -> None:
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines(keepends=True)
    header = re.compile(r"\[\s*(?:features|\"features\"|'features')\s*\]\s*(?:#.*)?")
    assignment = re.compile(
        r"([ \t]*(?:codex_hooks|\"codex_hooks\"|'codex_hooks')[ \t]*=[ \t]*)(?:false|true)([ \t]*(?:#[^\r\n]*)?\r?\n?)$"
    )
    for index, line in enumerate(lines):
        if not header.fullmatch(line.strip()):
            continue
        enabled = False
        for offset in range(index + 1, len(lines)):
            if lines[offset].lstrip().startswith("["):
                break
            if assignment.fullmatch(lines[offset]):
                lines[offset] = assignment.sub(r"\g<1>true\g<2>", lines[offset])
                enabled = True
                break
        if not enabled:
            if not line.endswith("\n"):
                lines[index] += "\n"
            lines.insert(index + 1, "codex_hooks = true\n")
        text = "".join(lines)
        break
    else:
        text += "\n[features]\ncodex_hooks = true\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text)


def register(client: Client, root: Path, host: Path, config: Path) -> None:
    match client:
        case "codex":
            host.mkdir(parents=True, exist_ok=True)
            catalog = root / ".agents/plugins/marketplace.json"
            catalog.parent.mkdir(parents=True, exist_ok=True)
            _ = catalog.write_text(
                json.dumps(
                    {
                        "name": "mem0-oss-releases",
                        "plugins": [
                            {
                                "name": "mem0-oss",
                                "source": {"source": "local", "path": "./current"},
                                "policy": {
                                    "installation": "AVAILABLE",
                                    "authentication": "ON_INSTALL",
                                },
                                "category": "Productivity",
                            }
                        ],
                    }
                )
            )
            environment = {**os.environ, "CODEX_HOME": str(host)}
            for args in (
                ["marketplace", "add", str(root)],
                ["add", "mem0-oss@mem0-oss-releases", "--json"],
            ):
                _ = subprocess.run(
                    ["codex", "plugin", *args],
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=True,
                )
            enable_hooks(host / "config.toml")
        case "opencode":
            loader = host / "plugins/mem0-oss.js"
            loader.parent.mkdir(parents=True, exist_ok=True)
            if loader.exists() and "mem0-oss-release-loader" not in loader.read_text():
                raise ReleaseError(
                    "existing OpenCode mem0-oss.js is not managed by this installer; move it aside first"
                )
            uri = (root / "current/dist/index.js").as_uri()
            text = (
                'const releaseLoader = "mem0-oss-release-loader";\n'
                f"process.env.MEM0_OSS_CLIENT_CONFIG ??= {json.dumps(str(config))};\n"
                f"const {{default: plugin}} = await import({json.dumps(uri)});\n"
                "export default plugin;\n"
            )
            temporary = loader.with_suffix(".tmp")
            _ = temporary.write_text(text)
            os.replace(temporary, loader)
        case unreachable:
            assert_never(unreachable)
