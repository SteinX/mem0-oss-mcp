# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: imported by install_client_release.py.
from __future__ import annotations

import hashlib
import os
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import TypedDict

from release_host import Client
from release_json import (
    JsonValue,
    ReleaseError,
    assert_never,
    json_integer,
    json_list,
    json_object,
    json_string,
    json_text,
    nullable_text,
    read_json,
)


class Snapshot(TypedDict):
    path: str
    content: str | None
    mode: int


class Transaction(TypedDict):
    current: str | None
    files: list[Snapshot]


class InstalledState(TypedDict):
    current: str
    previous: str | None
    host: str
    version: str


class Provenance(TypedDict):
    client: Client
    bridge_version: str


def read_transaction(path: Path) -> Transaction:
    value = read_json(path)
    files: list[Snapshot] = []
    for raw in json_list(value.get("files")):
        item = json_object(raw)
        raw_content = item.get("content")
        content = (
            None
            if raw_content is None
            else bytes.fromhex(json_string(raw_content)).hex()
        )
        mode = json_integer(item.get("mode"))
        if not 0 <= mode <= 0o777:
            raise ReleaseError("invalid transaction file mode")
        files.append(
            {"path": json_text(item.get("path")), "content": content, "mode": mode}
        )
    return {"current": nullable_text(value.get("current")), "files": files}


def read_installed_state(path: Path) -> InstalledState:
    value = read_json(path)
    return {
        "current": json_text(value.get("current")),
        "previous": nullable_text(value.get("previous")),
        "host": json_text(value.get("host")),
        "version": json_text(value.get("version")),
    }


def parse_client(value: JsonValue) -> Client:
    match value:
        case "codex" | "opencode" as client:
            return client
        case str() | int() | float() | list() | dict() | None:
            raise ReleaseError("invalid release client")
        case unreachable:
            assert_never(unreachable)


def read_provenance(path: Path) -> Provenance:
    value = read_json(path)
    return {
        "client": parse_client(value.get("client")),
        "bridge_version": json_text(value.get("bridge_version")),
    }


def atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        os.fchmod(handle.fileno(), mode)
        _ = handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def snapshots(paths: list[Path]) -> list[Snapshot]:
    return [
        {
            "path": str(path),
            "content": path.read_bytes().hex() if path.exists() else None,
            "mode": stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600,
        }
        for path in paths
    ]


def restore(files: list[Snapshot]) -> None:
    for snapshot in files:
        path = Path(snapshot["path"])
        content = snapshot["content"]
        if content is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, bytes.fromhex(content), snapshot["mode"])


def select_package(root: Path, target: str | None) -> None:
    current = root / "current"
    temporary = root / "current.tmp"
    temporary.unlink(missing_ok=True)
    if target is None:
        current.unlink(missing_ok=True)
        return
    temporary.symlink_to(target, target_is_directory=True)
    os.replace(temporary, current)


def extract_verified(archive: Path, directory: Path, client: str) -> str:
    is_tar = archive.name.endswith(".tar.gz")
    checksum = (
        archive.with_name(archive.name.removesuffix(".tar.gz") + ".sha256")
        if is_tar
        else archive.with_suffix(".sha256")
    )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    expected = f"{digest}  {archive.name}"
    if not checksum.is_file() or expected not in checksum.read_text().splitlines():
        raise ReleaseError("archive checksum missing or mismatched")
    names: set[str] = set()
    if is_tar:
        with tarfile.open(archive, "r:gz") as package:
            members = package.getmembers()
            if sum(member.size for member in members) > 100_000_000:
                raise ReleaseError("archive exceeds release size limit")
            for member in members:
                if not member.isfile():
                    raise ReleaseError("archive contains unsafe resources")
                target = resource_path(member.name, directory, names)
                handle = package.extractfile(member)
                if handle is None:
                    raise ReleaseError("archive contains unreadable resources")
                with handle:
                    content = handle.read()
                target.parent.mkdir(parents=True, exist_ok=True)
                _ = target.write_bytes(content)
    else:
        with zipfile.ZipFile(archive) as package:
            if sum(member.file_size for member in package.infolist()) > 100_000_000:
                raise ReleaseError("archive exceeds release size limit")
            for member in package.infolist():
                if stat.S_ISLNK(member.external_attr >> 16) or member.is_dir():
                    raise ReleaseError("archive contains unsafe resources")
                target = resource_path(member.filename, directory, names)
                target.parent.mkdir(parents=True, exist_ok=True)
                _ = target.write_bytes(package.read(member))
    provenance = read_provenance(directory / "provenance.json")
    if provenance["client"] != client:
        raise ReleaseError("archive client does not match installation target")
    return digest


def resource_path(name: str, directory: Path, names: set[str]) -> Path:
    path = PurePosixPath(name)
    normalized = path.as_posix()
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or not path.parts
        or path.parts[0] != "mem0-oss"
        or len(path.parts) < 2
        or normalized in names
    ):
        raise ReleaseError("archive contains unsafe or duplicate resources")
    names.add(normalized)
    return directory.joinpath(*path.parts[1:])
