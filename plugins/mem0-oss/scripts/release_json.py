# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: imported by the portable release modules.
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TypeAlias

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]


def assert_never(value: NoReturn) -> NoReturn:
    raise AssertionError(value)


@dataclass(frozen=True, slots=True)
class ReleaseError(ValueError):
    message: str

    def __str__(self) -> str:
        return self.message


def json_object(value: JsonValue) -> JsonObject:
    match value:
        case dict() as mapping:
            return mapping
        case str() | int() | float() | list() | None:
            raise ReleaseError("expected a JSON object")
        case unreachable:
            assert_never(unreachable)


def parse_json(content: str | bytes) -> JsonObject:
    value: JsonValue = json.loads(content)
    return json_object(value)


def read_json(path: Path) -> JsonObject:
    return parse_json(path.read_text(encoding="utf-8"))


def json_string(value: JsonValue) -> str:
    match value:
        case str() as text:
            return text
        case int() | float() | list() | dict() | None:
            raise ReleaseError("expected a string")
        case unreachable:
            assert_never(unreachable)


def json_text(value: JsonValue) -> str:
    text = json_string(value)
    if not text.strip() or re.search(r"[\x00-\x1f\x7f]", text):
        raise ReleaseError("expected a nonempty string without control characters")
    return text.strip()


def nullable_text(value: JsonValue) -> str | None:
    return None if value is None else json_text(value)


def json_list(value: JsonValue) -> list[JsonValue]:
    match value:
        case list() as entries:
            return entries
        case str() | int() | float() | dict() | None:
            raise ReleaseError("expected a JSON array")
        case unreachable:
            assert_never(unreachable)


def json_integer(value: JsonValue) -> int:
    match value:
        case bool():
            raise ReleaseError("expected an integer")
        case int() as number:
            return number
        case str() | float() | list() | dict() | None:
            raise ReleaseError("expected an integer")
        case unreachable:
            assert_never(unreachable)
