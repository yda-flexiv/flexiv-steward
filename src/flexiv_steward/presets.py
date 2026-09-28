"""Read CMake presets from the selected Flexiv source tree."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flexiv_steward.errors import ProjectError
from flexiv_steward.layout import source_root


@dataclass(frozen=True)
class Preset:
    name: str
    platform: str
    configure_name: str


def _preset_tables(project: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    source = source_root(project)
    configure: dict[str, dict[str, Any]] = {}
    build: dict[str, dict[str, Any]] = {}
    found = False
    for filename in ("CMakePresets.json", "CMakeUserPresets.json"):
        path = source / filename
        if not path.is_file():
            continue
        found = True
        try:
            data = json.loads(path.read_text())
            for key, table in (("configurePresets", configure), ("buildPresets", build)):
                for entry in data.get(key, []):
                    table[entry["name"]] = entry
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            raise ProjectError(f"could not read CMake presets: {path}") from error
    if not found:
        raise ProjectError(f"CMakePresets.json was not found in {source}")
    return configure, build


def _property(
    table: dict[str, dict[str, Any]], name: str, key: str, seen: frozenset[str] = frozenset()
) -> Any:
    if name in seen:
        raise ProjectError(f"cyclic CMake preset inheritance involving '{name}'")
    preset = table.get(name)
    if preset is None:
        raise ProjectError(f"CMake preset '{name}' was not found")
    if key in preset:
        return preset[key]
    parents = preset.get("inherits", [])
    if isinstance(parents, str):
        parents = [parents]
    for parent in parents:
        value = _property(table, parent, key, seen | {name})
        if value is not None:
            return value
    return None


def list_presets(project: Path) -> dict[str, list[str]]:
    """List public configure and build preset names from the current checkout."""
    configure, build = _preset_tables(project)
    return {
        "configure": [name for name, entry in configure.items() if not entry.get("hidden")],
        "build": [name for name, entry in build.items() if not entry.get("hidden")],
    }


def _toolchain(table: dict[str, dict[str, Any]], name: str, seen: frozenset[str] = frozenset()) -> Any:
    if name in seen:
        raise ProjectError(f"cyclic CMake preset inheritance involving '{name}'")
    entry = table.get(name)
    if entry is None:
        raise ProjectError(f"CMake preset '{name}' was not found")
    cache = entry.get("cacheVariables", {})
    if "CMAKE_TOOLCHAIN_FILE" in cache:
        value = cache["CMAKE_TOOLCHAIN_FILE"]
        return value.get("value") if isinstance(value, dict) else value
    parents = entry.get("inherits", [])
    if isinstance(parents, str):
        parents = [parents]
    for parent in parents:
        value = _toolchain(table, parent, seen | {name})
        if value is not None:
            return value
    return None


def configure_preset(project: Path, name: str) -> Preset:
    configure, _ = _preset_tables(project)
    entry = configure.get(name)
    if entry is None or entry.get("hidden"):
        raise ProjectError(f"unknown CMake configure preset '{name}'; run `fs preset list`")
    toolchain = _toolchain(configure, name)
    platform = "yocto" if name.startswith("yocto-") or (
        isinstance(toolchain, str) and "yocto" in toolchain.lower()
    ) else "ubuntu"
    return Preset(name, platform, name)


def build_preset(project: Path, name: str) -> Preset:
    configure, build = _preset_tables(project)
    entry = build.get(name)
    if entry is None or entry.get("hidden"):
        raise ProjectError(f"unknown CMake build preset '{name}'; run `fs preset list`")
    configure_name = _property(build, name, "configurePreset")
    if not isinstance(configure_name, str) or configure_name not in configure:
        raise ProjectError(f"CMake build preset '{name}' has no valid configurePreset")
    config = configure_preset(project, configure_name)
    return Preset(name, config.platform, configure_name)


def compile_commands_path(project: Path, configure_name: str) -> Path:
    configure, _ = _preset_tables(project)
    if configure_name not in configure:
        raise ProjectError(f"CMake configure preset '{configure_name}' was not found")
    binary_dir = _property(configure, configure_name, "binaryDir")
    if not isinstance(binary_dir, str):
        raise ProjectError(f"CMake configure preset '{configure_name}' has no binaryDir")
    source = source_root(project)
    return Path(binary_dir.replace("${sourceDir}", str(source))) / "compile_commands.json"
