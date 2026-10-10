#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: python3 install_pi_plugin.py --url https://mem0.example/mcp --install
"""Generate and optionally install the official Pi extension for the Mem0 OSS MCP bridge."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from install_opencode_plugin import (
    copy_adapter_file,
    copy_plugin,
    env_file_from_args,
    load_json,
    normalize_name,
    plugin_root_from_script,
    repo_root_from_script,
    validate_env_file,
    validate_env_var,
    write_json,
    write_token_env_file,
    validate_token_value,
)

from pi_runtime_patches import patch_runtime

from pi_plugin_layout import (
    Connection,
    InstallerError,
    patch_sources,
    validate_mcp_url,
    validate_source,
)


class Arguments(argparse.Namespace):
    url: str = ""
    name: str = "mem0-oss"
    api_key_env_var: str = "MEM0_OSS_MCP_TOKEN"
    api_key_stdin: bool = False
    api_key: str | None = None
    env_file: Path | None = None
    target_root: Path = Path.home() / ".mem0-oss/pi-plugins"
    upstream_plugin_dir: Path = repo_root_from_script() / "third_party/mem0"
    pi_dir: Path = Path(
        os.environ.get("OMO_CODING_AGENT_DIR")
        or os.environ.get("SENPI_CODING_AGENT_DIR")
        or os.environ.get("PI_CODING_AGENT_DIR")
        or Path.home() / ".pi/agent"
    )
    no_build: bool = False
    install: bool = False
    portable: bool = False


def parse_args() -> Arguments:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="", help="Absolute Mem0 OSS MCP /mcp URL; required unless --portable")
    parser.add_argument("--portable", action="store_true", help="Generate a distributable package configured at runtime")
    parser.add_argument("--name", default=Arguments.name)
    parser.add_argument(
        "--token-env-var",
        "--api-key-env-var",
        dest="api_key_env_var",
        default=Arguments.api_key_env_var,
    )
    tokens = parser.add_mutually_exclusive_group()
    tokens.add_argument(
        "--token-stdin",
        "--api-key-stdin",
        dest="api_key_stdin",
        action="store_true",
        help="Read MCP bearer token from stdin into a private env file",
    )
    tokens.add_argument(
        "--token",
        "--api-key",
        dest="api_key",
        help="MCP token value; prefer --token-stdin to avoid process listings",
    )
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--target-root", type=Path, default=Arguments.target_root)
    parser.add_argument(
        "--upstream-plugin-dir", type=Path, default=Arguments.upstream_plugin_dir
    )
    parser.add_argument("--pi-dir", type=Path, default=Arguments.pi_dir)
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Load source directly; Pi supplies host dependencies",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Register the package in Pi settings.json",
    )
    return parser.parse_args(namespace=Arguments())


def main() -> int:
    args = parse_args()
    if args.portable and (args.url or args.install or args.api_key_stdin or args.api_key is not None or args.env_file is not None):
        raise InstallerError("portable packages cannot include connection credentials, an endpoint or installation settings")
    name = normalize_name(args.name)
    source = validate_source(args.upstream_plugin_dir)
    base = args.target_root.expanduser().resolve()
    target = base / name
    if target == source or source in target.parents or target in source.parents:
        raise InstallerError(
            "target root must not overlap the upstream source directory"
        )
    token = None
    if args.api_key_stdin:
        token = validate_token_value(sys.stdin.read())
    elif args.api_key is not None:
        token = validate_token_value(args.api_key)
    env_file = env_file_from_args(args.env_file, base, name, token)
    validate_env_file(env_file, allow_missing=token is not None)
    if env_file is not None and (env_file == target or target in env_file.parents):
        raise InstallerError(
            "credential env file must be outside the generated package"
        )
    connection = Connection(
        validate_mcp_url(args.url) if not args.portable else "", validate_env_var(args.api_key_env_var), env_file
    )
    settings_path = args.pi_dir.expanduser().resolve() / "settings.json"
    settings = (
        load_json(settings_path) if args.install and settings_path.exists() else {}
    )
    packages = settings.get("packages", [])
    if not isinstance(packages, list):
        raise InstallerError("Pi settings packages must be a list")
    if args.install:
        if not any(
            entry == str(target)
            or isinstance(entry, dict)
            and entry.get("source") == str(target)
            for entry in packages
        ):
            packages.append(str(target))
        settings["packages"] = packages
    base.mkdir(parents=True, exist_ok=True)
    with (
        TemporaryDirectory(prefix=f".{name}-", dir=base) as staging_dir,
        ExitStack() as cleanup,
    ):
        staging = Path(staging_dir) / "plugin"
        copy_plugin(source, staging)
        shutil.copytree(
            source.parent / "agent-plugin-core/typescript/src",
            staging / "src/agent-plugin-core",
        )
        adapter = plugin_root_from_script() / "scripts/oss_adapter"
        for filename in (
            "mem0_oss_pi_env.ts",
            "mem0_oss_pi_client.ts",
            "mem0_oss_pi_transport.ts",
            "mem0_oss_pi_recall.ts",
            "mem0_oss_pi_listing.ts",
        ):
            copy_adapter_file(adapter / filename, staging / filename)
        patch_sources(staging, connection, portable=args.portable)
        patch_runtime(staging, None if args.portable else args.pi_dir.expanduser().resolve())
        package = load_json(staging / "package.json")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        package.update(name=f"@mem0-oss/{name}-pi-plugin", private=True)
        package["version"] = str(package["version"]).split("+", 1)[0] + f"+oss.{stamp}"
        package.get("dependencies", {}).pop("mem0ai", None)
        package.setdefault("dependencies", {})["zod"] = "^4.0.0"
        package["pi"]["extensions"] = [
            "./src/entry.ts" if args.no_build else "./dist/entry.js"
        ]
        write_json(staging / "package.json", package)
        if not args.no_build:
            subprocess.run(
                ["pnpm", "install", "--no-frozen-lockfile", "--ignore-scripts"],
                cwd=staging,
                check=True,
            )
            subprocess.run(["pnpm", "run", "build"], cwd=staging, check=True)
        staged_settings = None
        if args.install:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_dir = cleanup.enter_context(
                TemporaryDirectory(prefix=".mem0-settings-", dir=settings_path.parent)
            )
            staged_settings = Path(settings_dir) / "settings.json"
            write_json(staged_settings, settings)
            staged_settings.chmod(
                stat.S_IMODE(settings_path.stat().st_mode)
                if settings_path.exists()
                else 0o600
            )
        previous_key = (
            env_file.read_bytes()
            if token is not None and env_file is not None and env_file.exists()
            else None
        )
        previous_mode = (
            stat.S_IMODE(env_file.stat().st_mode)
            if env_file is not None and env_file.exists()
            else 0o600
        )
        backup = target.with_name(f"{name}.backup.{stamp}") if target.exists() else None
        promoted = False
        try:
            if token is not None and env_file is not None:
                write_token_env_file(env_file, connection.api_key_env_var, token)
            if backup is not None:
                target.rename(backup)
            staging.rename(target)
            promoted = True
            if staged_settings is not None:
                staged_settings.replace(settings_path)
        except OSError:
            if promoted:
                target.rename(staging)
            if backup is not None and backup.exists():
                backup.rename(target)
            if token is not None and env_file is not None:
                if previous_key is None:
                    env_file.unlink(missing_ok=True)
                else:
                    env_file.write_bytes(previous_key)
                    env_file.chmod(previous_mode)
            raise
        if backup is not None:
            print(f"Previous package retained: {backup}")
    if args.install:
        print(f"Registered Pi package in: {settings_path}")
    print(f"Generated Pi OSS plugin: {target}")
    print(f"OSS MCP URL: {connection.url or 'set MEM0_OSS_MCP_URL at runtime'}")
    if not args.install:
        print(f"Install with: pi install {target}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
