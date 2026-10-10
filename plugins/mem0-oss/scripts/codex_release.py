# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: called by build_client_releases.py.
from __future__ import annotations

import json
import shutil
from pathlib import Path

import install_codex_plugin as generator
from release_json import json_object, read_json


def generate_codex_release(upstream: Path, target: Path, version: str) -> None:
    source = generator.validate_upstream_plugin_dir(upstream)
    generator.copy_plugin(source, target)
    local = generator.plugin_root_from_script()
    generator.merge_local_oss_skill(local, target)
    generator.write_oss_adapter(local, target)
    generator.update_plugin_manifest(target, "mem0-oss", "Mem0 OSS")
    manifest = target / ".codex-plugin/plugin.json"
    value = read_json(manifest)
    value.update(version=version, hooks="./hooks/hooks.json")
    json_object(value.get("interface"))["longDescription"] = (
        "Full Mem0 OSS runtime, skills and native lifecycle hooks. Connection configuration is external."
    )
    _ = manifest.write_text(json.dumps(value, indent=2) + "\n")
    _ = (target / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "mem0": {
                        "command": "python3",
                        "args": ["${PLUGIN_ROOT}/scripts/release_mcp.py"],
                    }
                }
            },
            indent=2,
        )
        + "\n"
    )
    scripts = Path(__file__).resolve().parent
    for directory in (target / "core", target / "scripts"):
        for module in ("release_config.py", "release_json.py"):
            _ = shutil.copy2(scripts / module, directory / module)
    bootstrap = target / "core/mem0_oss_bootstrap.py"
    content = bootstrap.read_text()
    content = content.replace(
        "import mem0_oss_adapter\n",
        "import mem0_oss_adapter\nfrom release_config import load_codex_environment\n\nload_codex_environment()\n",
        1,
    )
    _ = bootstrap.write_text(content)
    _ = (target / "scripts/release_mcp.py").write_text(
        """from release_config import load_codex_environment
load_codex_environment()
from mem0_oss_stdio_bridge import main
raise SystemExit(main())
"""
    )
