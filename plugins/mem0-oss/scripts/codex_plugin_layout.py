from __future__ import annotations

import shlex
import shutil
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PluginLayoutError(ValueError):
    path: Path

    def __str__(self) -> str:
        return f"not a Mem0 plugin directory: {self.path}"


def upstream_plugin_directory(path: Path) -> Path:
    root = path.expanduser().resolve()
    for candidate in (root, root / "integrations/codex-plugin", root / "integrations/mem0-plugin"):
        if (candidate / ".codex-plugin/plugin.json").is_file():
            return candidate
    raise PluginLayoutError(root)


def hook_template_path(plugin_root: Path) -> Path:
    native = plugin_root / "hooks/hooks.json"
    return native if (plugin_root / "core").is_dir() and native.is_file() else plugin_root / "hooks/codex-hooks.json"


def write_native_oss_adapter(adapter_root: Path, plugin_root: Path) -> None:
    core = plugin_root / "core"
    if core.is_dir():
        shutil.copy2(adapter_root / "sitecustomize.py", core / "mem0_oss_adapter.py")
        shutil.copy2(adapter_root / "mem0_oss_native_sitecustomize.py", core / "mem0_oss_bootstrap.py")
        (core / "sitecustomize.py").write_text("import mem0_oss_bootstrap\n", encoding="utf-8")
        runtime = core / "memory_core.py"
        if runtime.is_file():
            content = runtime.read_text(encoding="utf-8")
            anchor = "from __future__ import annotations\n"
            add_anchor = '{**body, "messages": messages},\n                15,'
            if content.count(add_anchor) != 1:
                raise ValueError("native Mem0 flush contract changed; cannot safely adapt writes")
            count_anchor = 'def _result_count(response: dict[str, Any] | list[Any]) -> int:\n    if isinstance(response, dict):'
            if content.count(count_anchor) != 1:
                raise ValueError("native Mem0 event contract changed; cannot safely adapt counts")
            content = content.replace(anchor, anchor + "\nimport mem0_oss_bootstrap\n", 1)
            content = content.replace(add_anchor,
                '{**body, "messages": messages, "idempotency_key": '
                'mem0_oss_bootstrap.stable_batch_key(packet_id, index)},\n                90,', 1)
            content = content.replace(count_anchor, count_anchor +
                '\n        count = response.get("result_count")'
                '\n        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:'
                '\n            return count', 1)
            runtime.write_text(content, encoding="utf-8")
        for name in ("search", "remember"):
            target = plugin_root / "skills" / name / "SKILL.md"
            if target.is_file():
                shutil.copy2(adapter_root / name / "SKILL.md", target)


def bind_native_data_directory(plugin_root: Path, data_dir: Path) -> None:
    if not (plugin_root / "core").is_dir():
        return
    for name in (".mcp.json", ".codex-mcp.json"):
        path = plugin_root / name
        config = json.loads(path.read_text(encoding="utf-8"))
        for server in config["mcpServers"].values():
            server.setdefault("env", {})["MEM0_CODE_DATA_DIR"] = str(data_dir)
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def native_hook_variables(plugin_root: Path, data_dir: Path) -> list[str]:
    return [
        f"export PLUGIN_ROOT={shlex.quote(str(plugin_root))}",
        f"export PLUGIN_DATA={shlex.quote(str(data_dir))}",
        f"export PYTHONPATH={shlex.quote(str(plugin_root / 'core'))}:${{PYTHONPATH:-}}",
    ]
