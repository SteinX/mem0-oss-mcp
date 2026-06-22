#!/usr/bin/env python3
"""Generate and optionally install a Codex plugin for any Mem0 OSS MCP URL."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_MARKETPLACE_NAME = "mem0-oss-local"
DEFAULT_TOKEN_ENV_VAR = "MEM0_OSS_MCP_TOKEN"
DEFAULT_SERVER_NAME = "mem0"


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


def default_marketplace_root() -> Path:
    return Path.home() / ".mem0-oss-mcp" / "codex-plugins"


def plugin_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


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
    write_json(manifest_path, manifest)


def write_mcp_config(plugin_root: Path, server_name: str, url: str, token_env_var: str) -> None:
    write_json(
        plugin_root / ".mcp.json",
        {
            "mcpServers": {
                server_name: {
                    "url": url,
                    "bearer_token_env_var": token_env_var,
                }
            }
        },
    )


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
    parser.add_argument("--install", action="store_true", help="Run codex plugin marketplace add and codex plugin add")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plugin_name = normalize_name(args.name)
    server_name = normalize_name(args.server_name)
    marketplace_name = normalize_name(args.marketplace_name)
    url = validate_url(args.url)
    display_name = args.display_name or " ".join(part.capitalize() for part in plugin_name.split("-"))

    source_root = plugin_root_from_script()
    marketplace_root = args.marketplace_root.expanduser().resolve()
    target_root = marketplace_root / "plugins" / plugin_name

    copy_plugin(source_root, target_root)
    update_plugin_manifest(target_root, plugin_name, display_name)
    write_mcp_config(target_root, server_name, url, args.token_env_var)
    marketplace_path = update_marketplace(marketplace_root, marketplace_name, plugin_name)

    print(f"Generated plugin: {target_root}")
    print(f"Marketplace: {marketplace_path}")
    print(f"MCP URL: {url}")
    print(f"Token env var: {args.token_env_var}")

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
