# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: imported by the release package builders.
from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
import zipfile
from pathlib import Path


def write_archives(public: dict[str, bytes], output: Path, stem: str) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    archives = [output / f"{stem}.zip", output / f"{stem}.tar.gz"]
    with zipfile.ZipFile(archives[0], "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(public.items()):
            member = zipfile.ZipInfo(
                "mem0-oss/" + name, date_time=(1980, 1, 1, 0, 0, 0)
            )
            member.external_attr = 0o100644 << 16
            archive.writestr(member, data, compress_type=zipfile.ZIP_DEFLATED)
    with (
        archives[1].open("wb") as stream,
        gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for name, data in sorted(public.items()):
            member = tarfile.TarInfo("mem0-oss/" + name)
            member.size = len(data)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))
    checksum = output / f"{stem}.sha256"
    checksum.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in archives
        ),
        encoding="utf-8",
    )
    return [*archives, checksum]
