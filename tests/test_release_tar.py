from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "plugins/mem0-oss/scripts")
)
from release_archives import write_archives
from release_json import ReleaseError
from release_state import extract_verified


@pytest.mark.parametrize("client", ["codex", "opencode"])
def test_published_tar_archive_uses_shared_checksum_and_extracts(
    tmp_path: Path, client: str
) -> None:
    public = {
        "provenance.json": json.dumps(
            {"client": client, "bridge_version": "1.0.0"}
        ).encode(),
        "runtime/file": b"runtime",
    }
    archive = write_archives(public, tmp_path, "release")[1]
    digest = extract_verified(archive, tmp_path / "out", client)
    assert digest == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (tmp_path / "out/runtime/file").read_bytes() == b"runtime"


@pytest.mark.parametrize(
    "name,kind",
    [
        ("mem0-oss/../../outside", tarfile.REGTYPE),
        ("mem0-oss/link", tarfile.SYMTYPE),
        ("mem0-oss/link", tarfile.LNKTYPE),
        ("mem0-oss/device", tarfile.CHRTYPE),
    ],
)
def test_tar_archive_rejects_unsafe_resources(
    tmp_path: Path, name: str, kind: bytes
) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as package:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = "/tmp/outside"
        member.size = 3 if kind == tarfile.REGTYPE else 0
        package.addfile(member, io.BytesIO(b"bad") if member.size else None)
    (tmp_path / "unsafe.sha256").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    )
    with pytest.raises(ReleaseError, match="unsafe"):
        extract_verified(archive, tmp_path / "out", "codex")
    assert not (tmp_path / "out").exists()
