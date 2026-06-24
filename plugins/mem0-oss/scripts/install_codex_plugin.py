#!/usr/bin/env python3
"""Generate and optionally install a Codex plugin for any Mem0 OSS MCP URL.

MCP-only installs use this repository's lightweight plugin. Full-experience
installs copy the official Mem0 plugin from the third_party/mem0 submodule and
overlay only the Mem0 OSS compatibility files.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_MARKETPLACE_NAME = "mem0-oss-local"
DEFAULT_TOKEN_ENV_VAR = "MEM0_OSS_MCP_TOKEN"
DEFAULT_SERVER_NAME = "mem0"
HOOK_TEMPLATE = "codex-hooks.json"
UPSTREAM_PLUGIN_SUBDIR = "integrations/mem0-plugin"
MCP_TRANSPORTS = {"auto", "http", "stdio"}


def normalize_name(value: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    name = re.sub(r"-{2,}", "-", name).strip("-")
    if not name:
        raise ValueError("name must contain at least one ASCII letter or digit")
    return name


def validate_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("--url must be an absolute http(s) URL")
    if not parsed.path.rstrip("/").endswith("/mcp"):
        raise ValueError("--url should point to the bridge /mcp endpoint")
    return value.rstrip("/")


def validate_env_var(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("token env var must be a valid shell environment variable name")
    return value


def default_marketplace_root() -> Path:
    return Path.home() / ".mem0-oss-mcp" / "codex-plugins"


def plugin_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[3]


def default_upstream_plugin_dir() -> Path:
    return repo_root_from_script() / "third_party" / "mem0" / UPSTREAM_PLUGIN_SUBDIR


def validate_upstream_plugin_dir(path: Path) -> Path:
    plugin_dir = path.expanduser().resolve()
    if (plugin_dir / ".codex-plugin" / "plugin.json").is_file():
        return plugin_dir
    nested = plugin_dir / UPSTREAM_PLUGIN_SUBDIR
    if (nested / ".codex-plugin" / "plugin.json").is_file():
        return nested.resolve()
    raise ValueError(f"not a Mem0 plugin directory: {path}")


def copy_plugin(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.resolve()
    if source == target or source in target.parents:
        raise ValueError("marketplace root must not be inside the source plugin directory")
    if target.exists():
        shutil.rmtree(target)

    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {"__pycache__", ".pytest_cache"} or name.endswith((".pyc", ".pyo"))
        }

    shutil.copytree(source, target, ignore=ignore)


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def iter_hook_commands(config: dict):
    for entries in (config.get("hooks") or {}).values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for hook in entry.get("hooks", []):
                if isinstance(hook, dict):
                    yield hook


def update_plugin_manifest(plugin_root: Path, plugin_name: str, display_name: str) -> None:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = load_json(manifest_path)
    base_version = str(manifest.get("version", "0.1.0")).split("+", 1)[0]
    cachebuster = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    manifest["name"] = plugin_name
    manifest["version"] = f"{base_version}+codex.{cachebuster}"
    manifest["description"] = f"Connect Codex to {display_name}, a self-hosted Mem0 OSS MCP bridge."
    interface = manifest.setdefault("interface", {})
    interface["displayName"] = display_name
    interface["shortDescription"] = "Use a self-hosted Mem0 OSS MCP bridge from Codex."
    interface["longDescription"] = (
        f"{display_name} connects Codex to a self-hosted Mem0 OSS MCP bridge. "
        "This generated local copy points at your chosen bridge URL without "
        "committing hosts, ports, or tokens to the source repository."
    )
    manifest["mcpServers"] = "./.mcp.json"
    manifest.pop("hooks", None)
    write_json(manifest_path, manifest)


def selected_mcp_transport(requested: str, env_file: Path | None) -> str:
    if requested not in MCP_TRANSPORTS:
        raise ValueError(f"--mcp-transport must be one of: {', '.join(sorted(MCP_TRANSPORTS))}")
    if requested == "auto":
        return "stdio" if env_file is not None else "http"
    if requested == "stdio" and env_file is None:
        raise ValueError("--mcp-transport stdio requires --env-file")
    return requested


def write_mcp_config(
    plugin_root: Path,
    server_name: str,
    url: str,
    token_env_var: str,
    env_file: Path | None,
    mcp_transport: str,
) -> str:
    transport = selected_mcp_transport(mcp_transport, env_file)
    if transport == "stdio":
        bridge = plugin_root / "scripts" / "mem0_oss_stdio_bridge.py"
        config = {
            "mcpServers": {
                server_name: {
                    "command": "python3",
                    "args": [str(bridge)],
                    "env": {
                        "MEM0_OSS_MCP_URL": url,
                        "MEM0_OSS_MCP_TOKEN_ENV_VAR": token_env_var,
                        "MEM0_OSS_ENV_FILE": str(env_file),
                    },
                }
            }
        }
    else:
        config = {
            "mcpServers": {
                server_name: {
                    "url": url,
                    "bearer_token_env_var": token_env_var,
                }
            }
        }
    write_json(plugin_root / ".mcp.json", config)
    write_json(plugin_root / ".codex-mcp.json", config)
    return transport


def merge_local_oss_skill(source_root: Path, target_root: Path) -> None:
    source_skill = source_root / "skills" / "mem0-oss"
    if not source_skill.is_dir():
        return
    target_skill = target_root / "skills" / "mem0-oss"
    if target_skill.exists():
        shutil.rmtree(target_skill)
    shutil.copytree(source_skill, target_skill)


def copy_adapter_file(source: Path, target: Path, executable: bool = False) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if executable:
        target.chmod(0o755)


def write_stdio_bridge(source_root: Path, plugin_root: Path) -> None:
    adapter_root = source_root / "scripts" / "oss_adapter"
    copy_adapter_file(
        adapter_root / "mem0_oss_stdio_bridge.py",
        plugin_root / "scripts" / "mem0_oss_stdio_bridge.py",
        executable=True,
    )


def write_oss_adapter(source_root: Path, plugin_root: Path) -> None:
    adapter_root = source_root / "scripts" / "oss_adapter"
    scripts_dir = plugin_root / "scripts"
    copy_adapter_file(adapter_root / "sitecustomize.py", scripts_dir / "sitecustomize.py")
    copy_adapter_file(adapter_root / "mem0_oss_env.sh", scripts_dir / "mem0_oss_env.sh", executable=True)
    write_stdio_bridge(source_root, plugin_root)

    # These upstream background helpers are Platform-specific. Replacing the
    # generated copies avoids reaching api.mem0.ai while keeping upstream hook
    # scripts otherwise intact.
    for script in ("auto_import.py", "auto_setup_categories.py"):
        if (scripts_dir / script).exists():
            copy_adapter_file(adapter_root / "noop_platform_setup.py", scripts_dir / script, executable=True)

    onboard = plugin_root / "skills" / "onboard" / "SKILL.md"
    if onboard.exists():
        copy_adapter_file(adapter_root / "onboard" / "SKILL.md", onboard)


def update_marketplace(root: Path, marketplace_name: str, plugin_name: str) -> Path:
    marketplace_path = root / ".agents" / "plugins" / "marketplace.json"
    if marketplace_path.exists():
        marketplace = load_json(marketplace_path)
    else:
        marketplace = {
            "name": marketplace_name,
            "interface": {"displayName": "Mem0 OSS Local"},
            "plugins": [],
        }

    marketplace["name"] = marketplace_name
    marketplace.setdefault("interface", {}).setdefault("displayName", "Mem0 OSS Local")
    plugins = marketplace.setdefault("plugins", [])
    if not isinstance(plugins, list):
        raise ValueError("marketplace plugins field must be a list")

    entry = {
        "name": plugin_name,
        "source": {"source": "local", "path": f"./plugins/{plugin_name}"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Productivity",
    }
    for index, item in enumerate(plugins):
        if isinstance(item, dict) and item.get("name") == plugin_name:
            plugins[index] = entry
            break
    else:
        plugins.append(entry)

    write_json(marketplace_path, marketplace)
    return marketplace_path


def codex_dir_default() -> Path:
    return Path.home() / ".codex"


def load_hooks(path: Path) -> dict:
    if not path.exists():
        return {"hooks": {}}
    return load_json(path)


def command_env(name: str, value: str) -> str:
    return f"{name}={shlex.quote(value)}"


def codex_tool_segment(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def rewrite_hook_matcher(matcher: str, plugin_name: str, server_name: str) -> str:
    plugin_segment = codex_tool_segment(plugin_name)
    server_segment = codex_tool_segment(server_name)
    return (
        matcher.replace("mcp__plugin_mem0_mem0__", f"mcp__plugin_{plugin_segment}_{server_segment}__")
        .replace("mcp__mem0__", f"mcp__{server_segment}__")
    )


def rewrite_hook_matchers(config: dict, plugin_name: str, server_name: str) -> None:
    for entries in (config.get("hooks") or {}).values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            matcher = entry.get("matcher")
            if isinstance(matcher, str):
                entry["matcher"] = rewrite_hook_matcher(matcher, plugin_name, server_name)


def load_hook_template(
    plugin_root: Path,
    plugin_name: str,
    server_name: str,
    url: str,
    token_env_var: str,
    env_file: Path | None,
) -> dict:
    template_path = plugin_root / "hooks" / HOOK_TEMPLATE
    raw = template_path.read_text(encoding="utf-8")
    raw = raw.replace("${PLUGIN_ROOT}", shlex.quote(str(plugin_root)))
    template = json.loads(raw)
    rewrite_hook_matchers(template, plugin_name, server_name)

    scripts_dir = plugin_root / "scripts"
    prelude_parts = [
        f"export {command_env('MEM0_OSS_PLUGIN', plugin_name)}",
        f"export {command_env('MEM0_OSS_MCP_URL', url)}",
        f"export {command_env('MEM0_OSS_MCP_TOKEN_ENV_VAR', token_env_var)}",
        "export MEM0_TELEMETRY=false",
        f"export PYTHONPATH={shlex.quote(str(scripts_dir))}:${{PYTHONPATH:-}}",
    ]
    if env_file is not None:
        prelude_parts.append(f"export {command_env('MEM0_OSS_ENV_FILE', str(env_file))}")
    prelude_parts.append(f". {shlex.quote(str(scripts_dir / 'mem0_oss_env.sh'))}")
    prelude = "; ".join(prelude_parts)

    for hook in iter_hook_commands(template):
        command = hook.get("command")
        if isinstance(command, str):
            hook["command"] = f"bash -c {shlex.quote(prelude + '; ' + command)}"
    return template


def is_owned_hook_entry(entry: dict, plugin_name: str, plugin_root: Path) -> bool:
    markers = (
        f"MEM0_OSS_PLUGIN={plugin_name}",
        f"MEM0_OSS_PLUGIN='{plugin_name}'",
        f"{plugin_root}/scripts/",
        f"{shlex.quote(str(plugin_root))}/scripts/",
    )
    for hook in entry.get("hooks", []):
        command = hook.get("command", "") if isinstance(hook, dict) else ""
        if any(marker in command for marker in markers):
            return True
    return False


def strip_owned_hooks(config: dict, plugin_name: str, plugin_root: Path) -> dict:
    hooks = config.setdefault("hooks", {})
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        hooks[event] = [entry for entry in entries if not is_owned_hook_entry(entry, plugin_name, plugin_root)]
        if not hooks[event]:
            del hooks[event]
    return config


def merge_hooks(config: dict, template: dict) -> dict:
    hooks = config.setdefault("hooks", {})
    for event, entries in (template.get("hooks") or {}).items():
        hooks.setdefault(event, []).extend(entries)
    return config


def enable_codex_hooks_feature(config_file: Path) -> None:
    if config_file.exists():
        lines = config_file.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    in_features = False
    features_line: int | None = None
    codex_hooks_line: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_features = stripped == "[features]"
            if in_features:
                features_line = index
            continue
        if in_features and re.match(r"\s*codex_hooks\s*=", line):
            codex_hooks_line = index
            break

    if codex_hooks_line is not None:
        lines[codex_hooks_line] = "codex_hooks = true"
    elif features_line is not None:
        lines.insert(features_line + 1, "codex_hooks = true")
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[features]", "codex_hooks = true"])

    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def install_hooks(
    plugin_root: Path,
    plugin_name: str,
    server_name: str,
    url: str,
    token_env_var: str,
    codex_dir: Path,
    env_file: Path | None,
    enable_feature: bool,
) -> Path:
    if platform.system() == "Windows":
        raise RuntimeError("Codex hooks use bash scripts; install them from WSL or another Unix-like shell")

    if env_file is not None and not env_file.is_file():
        raise ValueError(f"--env-file does not exist: {env_file}")

    hooks_file = codex_dir / "hooks.json"
    config = load_hooks(hooks_file)
    template = load_hook_template(plugin_root, plugin_name, server_name, url, token_env_var, env_file)
    config = strip_owned_hooks(config, plugin_name, plugin_root)
    config = merge_hooks(config, template)
    write_json(hooks_file, config)

    if enable_feature:
        enable_codex_hooks_feature(codex_dir / "config.toml")

    return hooks_file


def run_codex_install(root: Path, marketplace_name: str, plugin_name: str) -> None:
    subprocess.run(["codex", "plugin", "marketplace", "add", str(root)], check=True)
    subprocess.run(["codex", "plugin", "add", f"{plugin_name}@{marketplace_name}"], check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Absolute bridge endpoint, for example http://host:8080/mcp")
    parser.add_argument("--name", default="mem0-oss", help="Generated plugin id, default: mem0-oss")
    parser.add_argument("--display-name", help="Display name shown in Codex")
    parser.add_argument("--server-name", default=DEFAULT_SERVER_NAME, help="MCP server name, default: mem0")
    parser.add_argument("--token-env-var", default=DEFAULT_TOKEN_ENV_VAR, help="Bearer token env var name")
    parser.add_argument("--marketplace-name", default=DEFAULT_MARKETPLACE_NAME, help="Local marketplace name")
    parser.add_argument("--marketplace-root", type=Path, default=default_marketplace_root(), help="Local marketplace root")
    parser.add_argument(
        "--with-hooks",
        action="store_true",
        help="Generate from the official Mem0 plugin and install Codex lifecycle hooks",
    )
    parser.add_argument(
        "--upstream-plugin-dir",
        type=Path,
        default=default_upstream_plugin_dir(),
        help="Official mem0 plugin directory, or a mem0 repo checkout containing integrations/mem0-plugin",
    )
    parser.add_argument("--codex-dir", type=Path, default=codex_dir_default(), help="Codex config directory for hooks")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional dotenv file used by hooks and, in auto/stdio transport, the MCP stdio bridge",
    )
    parser.add_argument(
        "--mcp-transport",
        choices=sorted(MCP_TRANSPORTS),
        default="auto",
        help="MCP connection mode. auto uses stdio when --env-file is set, otherwise http.",
    )
    parser.add_argument(
        "--no-enable-codex-hooks",
        action="store_true",
        help="Write hooks.json but do not set [features].codex_hooks = true",
    )
    parser.add_argument("--install", action="store_true", help="Run codex plugin marketplace add and codex plugin add")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plugin_name = normalize_name(args.name)
    server_name = normalize_name(args.server_name)
    marketplace_name = normalize_name(args.marketplace_name)
    url = validate_url(args.url)
    token_env_var = validate_env_var(args.token_env_var)
    display_name = args.display_name or " ".join(part.capitalize() for part in plugin_name.split("-"))

    local_source_root = plugin_root_from_script()
    marketplace_root = args.marketplace_root.expanduser().resolve()
    target_root = marketplace_root / "plugins" / plugin_name
    codex_dir = args.codex_dir.expanduser().resolve()
    env_file = args.env_file.expanduser().resolve() if args.env_file else None
    mcp_transport = selected_mcp_transport(args.mcp_transport, env_file)

    upstream_dir = args.upstream_plugin_dir.expanduser().resolve()
    default_upstream_dir = default_upstream_plugin_dir().resolve()
    using_upstream = args.with_hooks or upstream_dir != default_upstream_dir
    if using_upstream:
        source_root = validate_upstream_plugin_dir(upstream_dir)
        source_label = str(source_root)
    else:
        source_root = local_source_root
        source_label = "bundled MCP-only plugin"

    copy_plugin(source_root, target_root)
    if using_upstream:
        merge_local_oss_skill(local_source_root, target_root)
        write_oss_adapter(local_source_root, target_root)
    elif mcp_transport == "stdio":
        write_stdio_bridge(local_source_root, target_root)

    update_plugin_manifest(target_root, plugin_name, display_name)
    mcp_transport = write_mcp_config(target_root, server_name, url, token_env_var, env_file, mcp_transport)
    marketplace_path = update_marketplace(marketplace_root, marketplace_name, plugin_name)
    hooks_path: Path | None = None

    if args.with_hooks:
        hooks_path = install_hooks(
            target_root,
            plugin_name,
            server_name,
            url,
            token_env_var,
            codex_dir,
            env_file,
            enable_feature=not args.no_enable_codex_hooks,
        )

    print(f"Generated plugin: {target_root}")
    print(f"Source: {source_label}")
    print(f"Marketplace: {marketplace_path}")
    print(f"MCP URL: {url}")
    print(f"MCP transport: {mcp_transport}")
    print(f"Token env var: {token_env_var}")
    if hooks_path is not None:
        print(f"Hooks: {hooks_path}")
        print(f"Codex hooks feature: {'unchanged' if args.no_enable_codex_hooks else 'enabled'}")
        if env_file is not None:
            print(f"Hook env file: {env_file}")

    if args.install:
        run_codex_install(marketplace_root, marketplace_name, plugin_name)
        print(f"Installed: {plugin_name}@{marketplace_name}")
    else:
        print()
        print("Install with:")
        print(f"  codex plugin marketplace add {marketplace_root}")
        print(f"  codex plugin add {plugin_name}@{marketplace_name}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
