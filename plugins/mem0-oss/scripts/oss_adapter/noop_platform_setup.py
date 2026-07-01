#!/usr/bin/env python3
"""OSS-safe replacement for hosted Mem0 Platform category setup.

The official plugin runs a background helper that configures hosted Mem0
Platform project categories. Generated Mem0 OSS plugins do not need that call,
so the installer replaces ``auto_setup_categories.py`` with this file.

The pure helpers mirror the upstream module for tests and compatibility; the
entry point intentionally exits without contacting hosted Platform APIs.
"""

from __future__ import annotations

import hashlib
import json
import os

from setup_coding_categories import CODING_CATEGORIES, _categories_match


STATE_FILE = os.path.expanduser("~/.mem0/categories_setup.json")


def categories_fingerprint(categories: list = CODING_CATEGORIES) -> str:
    pairs = sorted(
        (str(key), str(value))
        for entry in categories
        if isinstance(entry, dict)
        for key, value in entry.items()
    )
    payload = json.dumps(pairs, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def apikey_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def load_state(path: str = STATE_FILE) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict, path: str = STATE_FILE) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)


def is_applied(state: dict, key_fp: str, cat_fp: str) -> bool:
    return state.get(key_fp) == cat_fp


def fetch_current_categories(client) -> list | None:
    current = client.project.get(fields=["custom_categories"])
    if isinstance(current, dict):
        return current.get("custom_categories")
    return None


def apply_categories(client, proposed: list = CODING_CATEGORIES) -> str:
    current = fetch_current_categories(client)
    if _categories_match(current, proposed):
        return "already-configured"
    client.project.update(custom_categories=proposed)
    return "applied"


def main() -> None:
    return None


if __name__ == "__main__":
    main()
