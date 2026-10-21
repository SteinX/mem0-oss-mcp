#!/usr/bin/env python3
"""Translate documented Devin CLI lifecycle payloads into Mem0's shared core."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import hook_runner
import mem0_oss_bootstrap
from memory_core import configure_harness, record_tool

configure_harness("devin", data_dir_name="devin-plugin", source_tag="devin_plugin")

_read_input = hook_runner.read_hook_input
_recover_pending = hook_runner.recover_pending_handoffs


def read_devin_input() -> dict:
    payload = _read_input()
    payload["cwd"] = (
        payload.get("cwd") or os.environ.get("DEVIN_PROJECT_DIR") or os.getcwd()
    )
    return payload


def recover_pending() -> int:
    return _recover_pending() if mem0_oss_bootstrap.capture_enabled() else 0


def post_tool(store, payload):
    response = payload.get("tool_response")
    success = response.get("success") if isinstance(response, dict) else None
    failed = not success if isinstance(success, bool) else None
    names = {
        "read": "Read",
        "write": "Write",
        "edit": "Edit",
        "exec": "Bash",
        "grep": "Grep",
        "glob": "Glob",
        "webfetch": "WebFetch",
        "notebook_edit": "NotebookEdit",
    }
    arguments = payload.get("tool_input")
    arguments = dict(arguments) if isinstance(arguments, dict) else {}
    if "path" in arguments and "file_path" not in arguments:
        arguments["file_path"] = arguments["path"]
    normalized = {
        **payload,
        "tool_name": names.get(payload.get("tool_name"), payload.get("tool_name")),
        "tool_input": arguments,
    }
    if failed:
        normalized["error"] = response
    record_tool(store, normalized, failed=failed)


if __name__ == "__main__":
    hook_runner.read_hook_input = read_devin_input
    hook_runner.recover_pending_handoffs = recover_pending
    if len(sys.argv) > 1 and sys.argv[1] == "post-tool":
        sys.argv[1] = "devin-post-tool"
    hook_runner.entry_point(
        extra_actions={"devin-post-tool": post_tool},
        automatic_flush_reasons={"session-end", "post-compaction"},
    )
