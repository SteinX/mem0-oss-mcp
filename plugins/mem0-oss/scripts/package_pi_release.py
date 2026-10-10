# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: python3 package_pi_release.py BUILT_PACKAGE OUTPUT_DIRECTORY VERSION SOURCE_SHA
"""Create release archives from an explicit public-file allowlist."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import sys
import tarfile
import zipfile
from pathlib import Path

from pi_plugin_layout import InstallerError


def package_release(
    plugin: Path, output: Path, version: str, source_sha: str
) -> list[Path]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or not re.fullmatch(
        r"[0-9a-f]{40}", source_sha
    ):
        raise InstallerError("invalid release version or source SHA")
    package = json.loads((plugin / "package.json").read_text(encoding="utf-8"))
    official_version = str(package["version"]).split("+", 1)[0]
    package.update(
        version=f"{official_version}+oss.mcp.{version}",
        dependencies={},
        devDependencies={},
    )
    for key in ("types", "scripts", "publishConfig"):
        package.pop(key, None)
    package["exports"] = {".": {"import": "./dist/index.js"}}
    package["pi"]["extensions"] = ["./dist/entry.js"]
    package["files"] = [
        "dist/*.js",
        "skills",
        "README.md",
        "LICENSE",
        "provenance.json",
    ]
    public = {"package.json": (json.dumps(package, indent=2) + "\n").encode()}
    files = sorted((plugin / "dist").glob("*.js")) + sorted(
        (plugin / "skills").rglob("SKILL.md")
    )
    if (plugin / "LICENSE").is_file():
        files.append(plugin / "LICENSE")
    if not (plugin / "dist/entry.js").is_file():
        raise InstallerError("built Pi entry is missing")
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(plugin.resolve()):
            raise InstallerError(
                "release resources must be regular files inside the generated package"
            )
        public[path.relative_to(plugin).as_posix()] = path.read_bytes()
    public["README.md"] = (
        Path(__file__).resolve().parents[3] / "docs/pi-release-package.md"
    ).read_bytes()
    public["provenance.json"] = (
        json.dumps(
            {
                "bridge_version": version,
                "generator_commit": source_sha,
                "official_pi_plugin_version": official_version,
                "configuration": "runtime",
            },
            indent=2,
        )
        + "\n"
    ).encode()
    output.mkdir(parents=True, exist_ok=True)
    stem = f"mem0-oss-pi-{version}"
    archive_zip = output / f"{stem}.zip"
    archive_tar = output / f"{stem}.tar.gz"
    with zipfile.ZipFile(archive_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(public.items()):
            member = zipfile.ZipInfo(
                "mem0-oss/" + name, date_time=(1980, 1, 1, 0, 0, 0)
            )
            member.external_attr = 0o100644 << 16
            archive.writestr(member, data, compress_type=zipfile.ZIP_DEFLATED)
    with (
        archive_tar.open("wb") as stream,
        gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for name, data in sorted(public.items()):
            member = tarfile.TarInfo("mem0-oss/" + name)
            member.size = len(data)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))
    archives = [archive_zip, archive_tar]
    checksum = output / f"{stem}.sha256"
    checksum.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in archives
        ),
        encoding="utf-8",
    )
    return [*archives, checksum]


if __name__ == "__main__":
    for result in package_release(
        Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
    ):
        print(result)
