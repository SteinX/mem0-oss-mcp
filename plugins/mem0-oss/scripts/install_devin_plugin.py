#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Generate and optionally install a local Devin CLI plugin for Mem0 OSS."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from devin_plugin_layout import generate, source_directory
from install_codex_plugin import (
    normalize_name,
    read_token_from_args,
    repo_root_from_script,
    validate_env_file,
    validate_env_var,
    write_token_env_file,
)
from release_config import validate_endpoint


def outside_package(path: Path, package: Path, label: str) -> None:
    if path == package or package in path.parents:
        raise ValueError(f"{label} must be outside the generated package")


def restore_credential(path: Path, previous: tuple[bytes, int] | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    content, mode = previous
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
    finally:
        if descriptor != -1:
            os.close(descriptor)


def cleanup_previous_plugin(backup: Path) -> None:
    try:
        shutil.rmtree(backup)
    except OSError as error:
        # Promotion has committed the new package and credential. A cleanup
        # failure must not roll either back, but the retained copy needs action.
        retry = shlex.join(
            [
                sys.executable,
                "-c",
                "import shutil, sys; shutil.rmtree(sys.argv[1])",
                str(backup),
            ]
        )
        print(
            f"Warning: plugin updated, but previous package cleanup failed. "
            f"Backup remains at: {backup}\n"
            f"Cleanup error: {error}\n"
            "Resolve the filesystem error (for example, directory permissions), "
            f"then retry cleanup with:\n{retry}",
            file=sys.stderr,
        )


def promote_plugin(
    staging: Path,
    target: Path,
    backup: Path,
    *,
    env_file: Path | None,
    token_env_var: str,
    token: str | None,
) -> None:
    previous_credential = None
    if token is not None and env_file is not None and env_file.exists():
        previous_credential = (
            env_file.read_bytes(),
            stat.S_IMODE(env_file.stat().st_mode),
        )
    credential_started = False
    package_moved = False
    try:
        if token is not None:
            assert env_file is not None
            credential_started = True
            write_token_env_file(env_file, token_env_var, token)
        if target.exists():
            target.rename(backup)
            package_moved = True
        staging.rename(target)
    except (OSError, ValueError) as error:
        rollback_errors = []
        if credential_started:
            try:
                assert env_file is not None
                restore_credential(env_file, previous_credential)
            except OSError:
                rollback_errors.append("credential restoration failed")
        if package_moved:
            try:
                backup.rename(target)
            except OSError:
                rollback_errors.append(f"previous package preserved at {backup}")
        if rollback_errors:
            raise OSError(
                "plugin update failed; " + "; ".join(rollback_errors)
            ) from error
        raise
    else:
        if package_moved:
            cleanup_previous_plugin(backup)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Mem0 OSS MCP /mcp endpoint")
    parser.add_argument("--name", default="mem0-oss")
    parser.add_argument(
        "--target-root", type=Path, default=Path.home() / ".mem0-oss-mcp/devin-plugins"
    )
    parser.add_argument(
        "--upstream-plugin-dir",
        type=Path,
        default=repo_root_from_script() / "third_party/mem0",
    )
    parser.add_argument("--token-env-var", default="MEM0_OSS_MCP_TOKEN")
    tokens = parser.add_mutually_exclusive_group()
    tokens.add_argument(
        "--token-stdin",
        action="store_true",
        help="Read the bearer token into a private env file",
    )
    tokens.add_argument(
        "--token", help="Prefer --token-stdin to keep the token out of process listings"
    )
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Persistent local evidence directory outside the package",
    )
    parser.add_argument(
        "--user-id",
        help="Concrete Mem0 user identity; defaults to the shared runtime's user resolution",
    )
    parser.add_argument(
        "--auto-capture",
        action="store_true",
        help="Enable background extraction at checkpoints and session end",
    )
    parser.add_argument(
        "--durable-writes",
        action="store_true",
        help="Use durable batch keys; requires a Sidecar-backed MCP bridge",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Run devin plugins install --local --yes on the generated bundle",
    )
    args = parser.parse_args()

    source = source_directory(args.upstream_plugin_dir)
    url = validate_endpoint(args.url)
    name = normalize_name(args.name)
    token_var = validate_env_var(args.token_env_var)
    base = args.target_root.expanduser().resolve()
    target = base / name
    if target.is_symlink():
        raise ValueError("generated package must not be a symlink")
    if target == source or source in target.parents or target in source.parents:
        raise ValueError("target root must not overlap the upstream plugin")
    if target.exists():
        marker = target / ".mem0-oss-runtime.json"
        if (
            not marker.is_file()
            or json.loads(marker.read_text()).get("generated_for") != "devin"
        ):
            raise ValueError("refusing to replace a directory not generated for Devin")
    if args.install and shutil.which("devin") is None:
        raise ValueError(
            "Devin CLI is not on PATH; omit --install to generate a reviewable plugin"
        )
    if args.user_id is not None:
        args.user_id = args.user_id.strip()
        if not args.user_id or args.user_id.casefold() in {"*", "all", "any"}:
            raise ValueError("--user-id must be a concrete identity")

    token = read_token_from_args(args)
    default_env = base / "env" / f"{name}.env"
    env_file = args.env_file or (
        default_env if token is not None or default_env.is_file() else None
    )
    if env_file is not None:
        env_file = env_file.expanduser().resolve()
        outside_package(env_file, target, "credential env file")
        validate_env_file(env_file, allow_missing=token is not None)
    data_dir = (args.data_dir or base / "data" / name).expanduser().resolve()
    outside_package(data_dir, target, "data directory")
    if (
        data_dir in target.parents
        or data_dir == env_file
        or (env_file and data_dir in env_file.parents)
    ):
        raise ValueError(
            "data directory must not contain the package or credential file"
        )
    config = {
        "url": url,
        "token_env_var": token_var,
        "data_dir": str(data_dir),
        "auto_capture": args.auto_capture,
        "durable_writes": args.durable_writes,
    }
    if env_file is not None:
        config["env_file"] = str(env_file)
    if args.user_id is not None:
        config["user_id"] = args.user_id

    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    with TemporaryDirectory(prefix=f".{name}-build-", dir=base) as directory:
        staging = Path(directory) / "plugin"
        generate(source, staging, target, name, config)
        # Validate and build everything before replacing a working plugin or key.
        # Keep the old package outside temporary cleanup if restoration itself
        # fails, so an unsuccessful update cannot silently delete its backup.
        backup = base / f".{name}-previous-{Path(directory).name}"
        promote_plugin(
            staging,
            target,
            backup,
            env_file=env_file,
            token_env_var=token_var,
            token=token,
        )
    print(f"Generated Devin plugin: {target}")
    if args.install:
        subprocess.run(
            ["devin", "plugins", "install", "--local", "--yes", str(target)], check=True
        )
    else:
        print(shlex.join(["devin", "plugins", "install", "--local", str(target)]))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Devin plugin generation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
