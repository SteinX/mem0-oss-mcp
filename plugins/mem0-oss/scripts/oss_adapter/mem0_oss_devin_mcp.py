#!/usr/bin/env python3
"""Expose the OSS bridge's complete MCP surface with the Devin connection."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import mem0_oss_bootstrap
import mem0_oss_stdio_bridge

mem0_oss_stdio_bridge.resolve_token = mem0_oss_bootstrap.resolve_token

if __name__ == "__main__":
    raise SystemExit(mem0_oss_stdio_bridge.main())
