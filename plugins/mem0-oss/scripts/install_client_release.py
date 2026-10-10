# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: python3 install_client_release.py install --client codex --archive PACKAGE.zip --url URL --token-stdin
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal

from release_config import (
    Connection,
    read_connection,
    validate_endpoint,
    validate_token_env_var,
)
from release_download import download_release
from release_host import Client, bind_codex, host_files, register
from release_json import ReleaseError, assert_never
from release_state import (
    InstalledState,
    Transaction,
    atomic_write,
    extract_verified,
    read_installed_state,
    read_provenance,
    read_transaction,
    restore,
    select_package,
    snapshots,
)


class InstallArguments(argparse.Namespace):
    action: Literal["install", "update", "rollback"] = "install"
    client: Client = "codex"
    root: Path = Path.home() / ".mem0-oss"
    host_dir: Path | None = None
    archive: Path | None = None
    version: str = "latest"
    url: str | None = None
    env_file: Path | None = None
    token_env_var: str | None = None
    token_stdin: bool = False


def install(args: InstallArguments) -> None:
    root = args.root.expanduser().resolve() / "clients" / args.client
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".install.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ReleaseError("another installation is in progress") from error
        run_transaction(args, root)


def run_transaction(args: InstallArguments, root: Path) -> None:
    journal = root / "transaction.json"
    if journal.exists():
        pending = read_transaction(journal)
        restore(pending["files"])
        select_package(root, pending["current"])
        journal.unlink()
    config = root / "connection.json"
    state_file = root / "installed.json"
    state = read_installed_state(state_file) if state_file.exists() else None
    match args.client:
        case "codex":
            default_host = Path.home() / ".codex"
        case "opencode":
            default_host = Path.home() / ".config/opencode"
        case unreachable:
            assert_never(unreachable)
    host = (
        (args.host_dir or (Path(state["host"]) if state else default_host))
        .expanduser()
        .resolve()
    )
    if state and str(host) != state["host"]:
        raise ReleaseError("use the original host directory for updates and rollback")
    connection: Connection
    if config.exists():
        connection = read_connection(config)
    else:
        if not args.url:
            raise ReleaseError("first installation requires --url")
        connection = {"url": validate_endpoint(args.url)}
    if args.url:
        connection["url"] = validate_endpoint(args.url)
    if args.env_file:
        env_file = args.env_file.expanduser().resolve()
        if not env_file.is_file() or env_file.is_relative_to(root / "versions"):
            raise ReleaseError("env file must exist outside the installed packages")
        connection["env_file"] = str(env_file)
    if args.token_env_var:
        connection["token_env_var"] = validate_token_env_var(args.token_env_var)
    token = sys.stdin.read().strip() if args.token_stdin else None
    credential = root / "credentials.env"
    if token is not None:
        if not token or any(char in token for char in "\r\n\0"):
            raise ReleaseError("invalid token input")
        connection["env_file"] = str(credential)
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=root) as temporary:
        staging = Path(temporary)
        match args.action:
            case "rollback":
                if state is None or state["previous"] is None:
                    raise ReleaseError("no previous release to roll back to")
                selected = Path(state["previous"])
                if not selected.is_dir():
                    raise ReleaseError("previous release is missing")
            case "install" | "update":
                archive = (
                    args.archive.expanduser().resolve()
                    if args.archive
                    else download_release(args.client, args.version, staging)
                )
                digest = extract_verified(archive, staging / "package", args.client)
                selected = root / "versions" / digest
                selected.parent.mkdir(exist_ok=True)
                if not selected.exists():
                    if args.client == "codex":
                        bind_codex(staging / "package", config, selected)
                    os.replace(staging / "package", selected)
            case unreachable:
                assert_never(unreachable)
        old = (
            str((root / "current").resolve())
            if (root / "current").is_symlink()
            else None
        )
        paths = [
            config,
            credential,
            state_file,
            *host_files(args.client, host),
            root / ".agents/plugins/marketplace.json",
        ]
        pending: Transaction = {"current": old, "files": snapshots(paths)}
        atomic_write(journal, json.dumps(pending).encode())
        try:
            if token is not None:
                name = connection.get("token_env_var", "MEM0_OSS_MCP_TOKEN")
                encoded = token.replace("\\", "\\\\").replace('"', '\\"')
                atomic_write(credential, f'{name}="{encoded}"\n'.encode())
            if not config.exists() or any(
                (args.url, args.env_file, args.token_env_var, args.token_stdin)
            ):
                atomic_write(config, (json.dumps(connection, indent=2) + "\n").encode())
            select_package(root, str(selected))
            register(args.client, root, host, config)
            provenance = read_provenance(selected / "provenance.json")
            installed: InstalledState = {
                "current": str(selected),
                "previous": old
                if old != str(selected)
                else (state["previous"] if state else None),
                "host": str(host),
                "version": provenance["bridge_version"],
            }
            atomic_write(
                state_file,
                json.dumps(installed).encode(),
            )
        except (OSError, ValueError, subprocess.CalledProcessError):
            restore(pending["files"])
            select_package(root, old)
            journal.unlink()
            raise
        journal.unlink()
        print(
            f"{args.client}: {args.action} complete ({provenance['bridge_version']}). Restart the host to load this release."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install, update or roll back prebuilt Mem0 OSS client releases; keep connections outside packages."
    )
    _ = parser.add_argument("action", choices=["install", "update", "rollback"])
    _ = parser.add_argument("--client", choices=["codex", "opencode"], required=True)
    _ = parser.add_argument("--root", type=Path, default=Path.home() / ".mem0-oss")
    _ = parser.add_argument("--host-dir", type=Path)
    source = parser.add_mutually_exclusive_group()
    _ = source.add_argument("--archive", type=Path)
    _ = source.add_argument("--version", default="latest")
    _ = parser.add_argument("--url")
    _ = parser.add_argument("--env-file", type=Path)
    _ = parser.add_argument("--token-env-var")
    _ = parser.add_argument("--token-stdin", action="store_true")
    args = parser.parse_args(namespace=InstallArguments())
    if args.action == "rollback" and any(
        (args.archive, args.url, args.env_file, args.token_env_var, args.token_stdin)
    ):
        parser.error(
            "rollback preserves configuration and accepts no connection or archive options"
        )
    try:
        install(args)
    except ReleaseError as error:
        print(f"installation failed: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(
            f"installation failed: {type(error).__name__}; prior installation restored if a transaction was started",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
