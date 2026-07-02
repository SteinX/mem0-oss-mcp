#!/usr/bin/env python3
"""Generate and optionally install an OpenCode plugin for a Mem0 OSS MCP URL.

The generated plugin is copied from the official Mem0 OpenCode plugin, then a
small compatibility client is overlaid so the plugin's native tools and hooks
call a self-hosted mem0-oss-mcp bridge instead of the hosted Mem0 Platform API.
"""

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


DEFAULT_PLUGIN_NAME = "mem0-oss"
DEFAULT_TOKEN_ENV_VAR = "MEM0_OSS_MCP_TOKEN"
UPSTREAM_OPENCODE_SUBDIR = "integrations/mem0-plugin/.opencode-plugin"


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


def plugin_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[3]


def default_upstream_opencode_plugin_dir() -> Path:
    return repo_root_from_script() / "third_party" / "mem0" / UPSTREAM_OPENCODE_SUBDIR


def default_target_root() -> Path:
    return Path.home() / ".mem0-oss-mcp" / "opencode-plugins"


def default_opencode_dir() -> Path:
    return Path.home() / ".config" / "opencode"


def validate_upstream_opencode_plugin_dir(path: Path) -> Path:
    root = path.expanduser().resolve()
    candidates = [
        root,
        root / ".opencode-plugin",
        root / UPSTREAM_OPENCODE_SUBDIR,
    ]
    for candidate in candidates:
        if (candidate / "package.json").is_file() and (candidate / "opencode-mem0.ts").is_file():
            return candidate
    raise ValueError(f"not a Mem0 OpenCode plugin directory: {path}")


def validate_env_file(path: Path | None) -> None:
    if path is not None and not path.is_file():
        raise ValueError(f"--env-file does not exist: {path}")


def copy_plugin(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("target root must not be inside the source plugin directory")
    if target.exists():
        shutil.rmtree(target)

    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {"node_modules", "dist", "__pycache__", ".pytest_cache"} or name.endswith((".pyc", ".pyo"))
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


def copy_adapter_file(source: Path, target: Path, executable: bool = False) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if executable:
        target.chmod(0o755)


def write_oss_adapter(source_root: Path, plugin_root: Path) -> None:
    adapter_root = source_root / "scripts" / "oss_adapter"
    copy_adapter_file(adapter_root / "mem0_oss_memory_client.ts", plugin_root / "mem0_oss_memory_client.ts")


def update_package_json(plugin_root: Path, plugin_name: str, display_name: str) -> None:
    package_path = plugin_root / "package.json"
    package = load_json(package_path)
    base_version = str(package.get("version", "0.1.0")).split("+", 1)[0]
    cachebuster = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    package["name"] = f"@mem0-oss/{plugin_name}-opencode-plugin"
    package["version"] = f"{base_version}+oss.{cachebuster}"
    package["private"] = True
    package["description"] = f"{display_name} OpenCode plugin for a self-hosted Mem0 OSS MCP bridge"
    dependencies = package.get("dependencies")
    if isinstance(dependencies, dict):
        dependencies.pop("mem0ai", None)
    write_json(package_path, package)


def js_literal(value: str | None) -> str:
    if value is None:
        return "undefined"
    return json.dumps(value)


def patch_opencode_source(plugin_root: Path, url: str, token_env_var: str, env_file: Path | None) -> None:
    source_path = plugin_root / "opencode-mem0.ts"
    content = source_path.read_text(encoding="utf-8")
    content, import_count = re.subn(
        r'import\s*\{\s*MemoryClient\s*\}\s*from\s*["\']mem0ai["\'];',
        'import {MemoryClient, initializeMem0OssEnv} from "./mem0_oss_memory_client";',
        content,
        count=1,
    )
    if import_count != 1:
        raise ValueError("OpenCode plugin source did not contain the expected mem0ai MemoryClient import")

    anchor = "  const {$, client} = ctx;\n"
    if anchor not in content:
        raise ValueError("OpenCode plugin source did not contain the expected ctx destructuring anchor")
    env_file_value = str(env_file) if env_file is not None else None
    init = (
        anchor
        + "  initializeMem0OssEnv({\n"
        + f"    url: {js_literal(url)},\n"
        + f"    tokenEnvVar: {js_literal(token_env_var)},\n"
        + f"    envFile: {js_literal(env_file_value)},\n"
        + "  });\n"
    )
    content = content.replace(anchor, init, 1)
    source_path.write_text(content, encoding="utf-8")


def run_bun(plugin_root: Path, args: list[str]) -> None:
    bun = shutil.which("bun")
    if not bun:
        raise RuntimeError("bun is required to build the generated OpenCode plugin")
    subprocess.run([bun, *args], cwd=plugin_root, check=True)


def build_plugin(plugin_root: Path) -> None:
    run_bun(plugin_root, ["install"])
    run_bun(plugin_root, ["run", "build"])


def install_local_loader(plugin_root: Path, plugin_name: str, opencode_dir: Path) -> Path:
    dist_entry = plugin_root / "dist" / "index.js"
    if not dist_entry.is_file():
        raise RuntimeError(f"built plugin entry does not exist: {dist_entry}")

    plugins_dir = opencode_dir / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    loader = plugins_dir / f"{plugin_name}.js"
    loader.write_text(
        "\n".join(
            [
                f'export {{ default }} from "{dist_entry.as_uri()}";',
                f'export * from "{dist_entry.as_uri()}";',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return loader


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Absolute bridge endpoint, for example http://host:8080/mcp")
    parser.add_argument("--name", default=DEFAULT_PLUGIN_NAME, help="Generated plugin id, default: mem0-oss")
    parser.add_argument("--display-name", help="Display name used in generated package metadata")
    parser.add_argument("--token-env-var", default=DEFAULT_TOKEN_ENV_VAR, help="Bearer token env var name")
    parser.add_argument(
        "--target-root",
        type=Path,
        default=default_target_root(),
        help="Directory where generated OpenCode plugin copies are stored",
    )
    parser.add_argument(
        "--upstream-plugin-dir",
        type=Path,
        default=default_upstream_opencode_plugin_dir(),
        help="Official .opencode-plugin directory, or a mem0 checkout containing integrations/mem0-plugin/.opencode-plugin",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional dotenv file read by the generated plugin for the bridge token",
    )
    parser.add_argument(
        "--opencode-dir",
        type=Path,
        default=default_opencode_dir(),
        help="OpenCode config directory for --install, default ~/.config/opencode",
    )
    parser.add_argument("--no-build", action="store_true", help="Generate files but skip bun install/build")
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install a local loader into the OpenCode plugin directory after building",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plugin_name = normalize_name(args.name)
    url = validate_url(args.url)
    token_env_var = validate_env_var(args.token_env_var)
    display_name = args.display_name or " ".join(part.capitalize() for part in plugin_name.split("-"))
    env_file = args.env_file.expanduser().resolve() if args.env_file else None
    validate_env_file(env_file)

    local_source_root = plugin_root_from_script()
    source_root = validate_upstream_opencode_plugin_dir(args.upstream_plugin_dir)
    target_root = args.target_root.expanduser().resolve() / plugin_name
    opencode_dir = args.opencode_dir.expanduser().resolve()

    copy_plugin(source_root, target_root)
    write_oss_adapter(local_source_root, target_root)
    patch_opencode_source(target_root, url, token_env_var, env_file)
    update_package_json(target_root, plugin_name, display_name)

    if not args.no_build:
        build_plugin(target_root)

    loader_path: Path | None = None
    if args.install:
        loader_path = install_local_loader(target_root, plugin_name, opencode_dir)

    print(f"Generated OpenCode plugin: {target_root}")
    print(f"Source: {source_root}")
    print(f"MCP URL: {url}")
    print(f"Token env var: {token_env_var}")
    if env_file is not None:
        print(f"Env file: {env_file}")
    if args.no_build:
        print("Build: skipped")
    else:
        print(f"Built entry: {target_root / 'dist' / 'index.js'}")
    if loader_path is not None:
        print(f"Installed OpenCode loader: {loader_path}")
    else:
        print()
        print("Install after building with:")
        print(f"  mkdir -p {opencode_dir / 'plugins'}")
        print(f"  printf '%s\\n' 'export {{ default }} from \"{(target_root / 'dist' / 'index.js').as_uri()}\";' > {opencode_dir / 'plugins' / (plugin_name + '.js')}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
