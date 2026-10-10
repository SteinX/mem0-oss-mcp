# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: imported by install_client_release.py.
from __future__ import annotations

import re
import urllib.request
from http.client import HTTPResponse
from pathlib import Path

from release_host import Client
from release_json import ReleaseError, json_text, parse_json


def download_release(client: Client, version: str, output: Path) -> Path:
    if version == "latest":
        response: HTTPResponse = urllib.request.urlopen(
            "https://api.github.com/repos/SteinX/mem0-oss-mcp/releases/latest",
            timeout=30,
        )
        with response:
            version = json_text(parse_json(response.read(1_000_000)).get("tag_name"))
    if not re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ReleaseError("invalid release version")
    stem = f"mem0-oss-{client}-{version.removeprefix('v')}"
    for suffix in ("zip", "sha256"):
        path = output / f"{stem}.{suffix}"
        response = urllib.request.urlopen(
            f"https://github.com/SteinX/mem0-oss-mcp/releases/download/{version}/{path.name}",
            timeout=60,
        )
        with response:
            content = response.read(100_000_001)
        if len(content) > 100_000_000:
            raise ReleaseError("release download exceeds size limit")
        _ = path.write_bytes(content)
    return output / f"{stem}.zip"
