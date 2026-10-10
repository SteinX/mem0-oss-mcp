# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: python3 build_client_releases.py VERSION OUTPUT_DIRECTORY --upstream MEM0_CHECKOUT
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

from codex_release import generate_codex_release
from release_archives import write_archives
from release_host import Client
from release_json import ReleaseError, assert_never

SCRIPTS: Final = Path(__file__).resolve().parent
REPO: Final = SCRIPTS.parents[2]
INSTALLER_FILES: Final = [
    "install_client_release.py",
    "release_config.py",
    "release_state.py",
    "release_host.py",
    "release_download.py",
    "release_json.py",
]


class BuildArguments(argparse.Namespace):
    version: str = ""
    output: Path = Path.cwd()
    upstream: Path = REPO / "third_party/mem0"


def package_client(
    plugin: Path,
    client: Client,
    output: Path,
    version: str,
    source_sha: str,
    license_file: Path,
) -> list[Path]:
    public = {
        "LICENSE": license_file.read_bytes(),
        "OSS_LICENSE": (REPO / "LICENSE").read_bytes(),
        "README.md": (REPO / "docs/client-release-packages.md").read_bytes(),
    }
    for name in INSTALLER_FILES:
        public["install.py" if name == "install_client_release.py" else name] = (
            SCRIPTS / name
        ).read_bytes()
    match client:
        case "codex":
            files = [
                plugin / ".codex-plugin/plugin.json",
                plugin / ".mcp.json",
                plugin / "hooks/hooks.json",
            ]
            files += sorted((plugin / "core").glob("*.py")) + sorted(
                (plugin / "scripts").glob("*.py")
            )
            files += sorted((plugin / "hooks").glob("*.py")) + sorted(
                (plugin / "skills").rglob("*.md")
            )
        case "opencode":
            if not (plugin / "dist/index.js").is_file():
                raise ReleaseError("built OpenCode entry missing")
            files = sorted((plugin / "dist").glob("*.js")) + sorted(
                (plugin / "opencode-skills").rglob("*.md")
            )
            for dependency in ("zod", "@opencode-ai/plugin", "@opencode-ai/sdk"):
                license_path = plugin / "node_modules" / dependency / "LICENSE"
                if license_path.is_file():
                    public[f"licenses/{dependency.replace('/', '-')}.txt"] = (
                        license_path.read_bytes()
                    )
            public["package.json"] = (
                json.dumps(
                    {
                        "name": "@mem0-oss/opencode-plugin",
                        "version": version,
                        "type": "module",
                        "main": "./dist/index.js",
                        "exports": {".": "./dist/index.js"},
                        "dependencies": {},
                        "license": "Apache-2.0",
                    },
                    indent=2,
                )
                + "\n"
            ).encode()
        case unreachable:
            assert_never(unreachable)
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(plugin.resolve()):
            raise ReleaseError(
                "release resources must be regular files inside the generated package"
            )
        public[path.relative_to(plugin).as_posix()] = path.read_bytes()
    public["provenance.json"] = (
        json.dumps(
            {
                "client": client,
                "bridge_version": version,
                "generator_commit": source_sha,
                "configuration": "external",
            },
            indent=2,
        )
        + "\n"
    ).encode()
    return write_archives(public, output, f"mem0-oss-{client}-{version}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build portable full Codex and OpenCode release packages"
    )
    _ = parser.add_argument("version")
    _ = parser.add_argument("output", type=Path)
    _ = parser.add_argument("--upstream", type=Path, default=REPO / "third_party/mem0")
    args = parser.parse_args(namespace=BuildArguments())
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version):
        raise ReleaseError("invalid release version")
    source_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ReleaseError("invalid source revision")
    with tempfile.TemporaryDirectory(prefix="mem0-client-build-") as temporary:
        staging = Path(temporary)
        codex = staging / "codex"
        generate_codex_release(
            args.upstream, codex, f"{args.version}+oss.{source_sha[:12]}"
        )
        for path in package_client(
            codex,
            "codex",
            args.output,
            args.version,
            source_sha,
            args.upstream / "LICENSE",
        ):
            print(path)
        _ = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "install_opencode_plugin.py"),
                "--portable",
                "--upstream-plugin-dir",
                str(args.upstream),
                "--target-root",
                str(staging / "opencode"),
                "--no-build",
            ],
            check=True,
        )
        opencode = staging / "opencode/mem0-oss"
        bun = shutil.which("bun")
        if not bun:
            raise ReleaseError("Bun is required to build OpenCode releases")
        _ = subprocess.run(
            [bun, "install", "--ignore-scripts"], cwd=opencode, check=True
        )
        _ = subprocess.run(
            [
                bun,
                "build",
                "opencode-mem0.ts",
                "--outfile",
                "dist/index.js",
                "--target",
                "bun",
                "--format",
                "esm",
            ],
            cwd=opencode,
            check=True,
        )
        for path in package_client(
            opencode,
            "opencode",
            args.output,
            args.version,
            source_sha,
            args.upstream / "LICENSE",
        ):
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
