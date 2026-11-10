"""Read CMake presets from the selected Flexiv source tree."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flexiv_steward.errors import ProjectError
from flexiv_steward.layout import source_root
from flexiv_steward.preset_store import (
    current_path,
    load_current,
    load_history,
    workspace_of,
)


@dataclass(frozen=True)
class Preset:
    name: str
    platform: str
    configure_name: str


@dataclass(frozen=True)
class PresetChoice:
    """A preset picked for one command, with where the choice came from.

    ``origin`` is ``"argument"`` for an explicit command-line preset,
    ``"worktree"`` for the preset remembered in ``<worktree>/.steward/preset.json``
    and ``"history"`` for the workspace's most used configure preset. ``notes``
    carries user-facing remarks, such as a stale remembered preset that was
    skipped, so the CLI can explain a surprising default.
    """

    preset: Preset
    origin: str
    notes: tuple[str, ...] = ()


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


def matching_build_preset(project: Path, configure_name: str) -> str | None:
    """Return the public build preset that configures *configure_name*, if any.

    A build preset sharing the configure preset's name wins, because the Flexiv
    presets keep the pair aligned. Otherwise the first public build preset in
    file order pointing at the configure preset is returned, so a renamed build
    preset still builds the remembered configuration.
    """
    configure, build = _preset_tables(project)
    entry = configure.get(configure_name)
    if entry is None or entry.get("hidden"):
        return None
    candidates = [name for name, preset in build.items() if not preset.get("hidden")]
    ordered = candidates
    if configure_name in candidates:
        ordered = [configure_name, *(name for name in candidates if name != configure_name)]
    for name in ordered:
        try:
            if _property(build, name, "configurePreset") == configure_name:
                return name
        except ProjectError:
            # A broken or cyclic preset cannot match; keep looking.
            continue
    return None


def _with_notes(notes: list[str], message: str) -> str:
    """Keep the skipped-preset explanations visible when resolution fails."""
    return "; ".join([*notes, message]) if notes else message


def resolve_configure_preset(project: Path, requested: str | None) -> PresetChoice:
    """Pick the configure preset for ``fs setup`` without requiring an argument.

    Precedence: the explicit argument, then the preset this worktree remembered,
    then the workspace's most used configure preset.
    """
    if requested:
        return PresetChoice(configure_preset(project, requested), "argument")
    notes: list[str] = []
    remembered = load_current(project)
    if remembered is not None:
        try:
            return PresetChoice(
                configure_preset(project, remembered.configure_preset), "worktree", tuple(notes)
            )
        except ProjectError:
            notes.append(f"ignoring stale remembered preset '{remembered.configure_preset}'")
    for usage in load_history(workspace_of(project)):
        try:
            preset = configure_preset(project, usage.name)
        except ProjectError:
            continue
        return PresetChoice(preset, "history", tuple(notes))
    raise ProjectError(_with_notes(
        notes,
        "no CMake configure preset was given, and no remembered or most-used preset is available; "
        "run `fs preset list`, then `fs setup <preset>`",
    ))


def resolve_build_preset(project: Path, requested: str | None) -> PresetChoice:
    """Pick the build preset for ``fs build`` without requiring an argument.

    Only the current worktree can supply the default: a build must reuse the
    preset ``fs setup`` configured, so workspace history is deliberately not
    consulted here.
    """
    if requested:
        return PresetChoice(build_preset(project, requested), "argument")
    notes: list[str] = []
    remembered = load_current(project)
    if remembered is not None:
        if remembered.preset:
            try:
                return PresetChoice(build_preset(project, remembered.preset), "worktree", tuple(notes))
            except ProjectError:
                notes.append(f"ignoring stale remembered build preset '{remembered.preset}'")
        try:
            configure_preset(project, remembered.configure_preset)
        except ProjectError:
            notes.append(f"ignoring stale remembered preset '{remembered.configure_preset}'")
        else:
            mapped = _mapped_build_preset(project, remembered.configure_preset)
            if mapped is not None:
                if mapped != remembered.configure_preset:
                    notes.append(
                        f"build preset '{mapped}' configures remembered preset "
                        f"'{remembered.configure_preset}'"
                    )
                return PresetChoice(build_preset(project, mapped), "worktree", tuple(notes))
            notes.append(
                f"no build preset configures remembered preset '{remembered.configure_preset}'"
            )
    raise ProjectError(_with_notes(
        notes,
        "no CMake build preset was given and no usable preset is remembered in "
        f"{current_path(project)}; run `fs setup <preset>` or pass a build preset "
        "(see `fs preset list`)",
    ))


def _mapped_build_preset(project: Path, configure_name: str) -> str | None:
    """Return the build preset for *configure_name*, tolerating broken preset files."""
    try:
        return matching_build_preset(project, configure_name)
    except ProjectError:
        return None


def remember_preset(project: Path, name: str) -> tuple[Preset, str | None]:
    """Resolve one ``fs preset use`` name into its configure and build presets.

    The name may be either a configure or a build preset, so a name that only
    exists in one table is still accepted. The returned build preset is ``None``
    when no public build preset configures that configuration.
    """
    try:
        configure = configure_preset(project, name)
    except ProjectError:
        pass
    else:
        return configure, _mapped_build_preset(project, configure.name)
    try:
        build = build_preset(project, name)
    except ProjectError:
        raise ProjectError(f"unknown CMake preset '{name}'; run `fs preset list`") from None
    return configure_preset(project, build.configure_name), build.name


def remembered_configure_preset(project: Path) -> tuple[Preset | None, tuple[str, ...]]:
    """Return the remembered preset ``fs run`` may use for its build directory.

    ``(None, notes)`` means the built-in default directory stays in charge: no
    preset is remembered, the remembered name is stale, or it is not an Ubuntu
    build that this host launch supports. ``notes`` explains the skips.
    """
    remembered = load_current(project)
    if remembered is None:
        return None, ()
    try:
        preset = configure_preset(project, remembered.configure_preset)
    except ProjectError:
        return None, (f"ignoring stale remembered preset '{remembered.configure_preset}'",)
    if preset.platform != "ubuntu":
        return None, (
            f"remembered preset '{preset.name}' is not an Ubuntu build; "
            "using the default build directory",
        )
    return preset, ()
