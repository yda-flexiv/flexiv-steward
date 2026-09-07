"""JSON-safe serialization for immutable collection models."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import TypeAlias

from flexiv_steward.models import BuildDirectory


JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


def to_jsonable(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BuildDirectory):
        return {
            "directory": value.directory,
            "source_directory": value.source_directory,
            "build_type": value.build_type,
            "generator": value.generator,
            "presets": list(value.presets),
            "options": dict(value.options),
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    raise TypeError(f"cannot serialize {type(value).__name__}")
