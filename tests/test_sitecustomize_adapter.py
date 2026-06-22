from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_DIR = REPO_ROOT / "plugins" / "mem0-oss" / "scripts" / "oss_adapter"


def test_sitecustomize_imports_and_patches_urllib() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ADAPTER_DIR)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import urllib.request; print(urllib.request.urlopen.__module__)",
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "sitecustomize"
