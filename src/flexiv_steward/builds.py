"""Discover configured CMake build directories and open their cache in cmake-gui."""

from __future__ import annotations

import json
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from flexiv_steward.errors import ProjectError
from flexiv_steward.layout import source_root
from flexiv_steward.models import BuildDirectory
from flexiv_steward.process import run_process


def _read_cache(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(errors="replace").splitlines():
        if not line or line.startswith("//") or line.startswith("#") or ":" not in line or "=" not in line:
            continue
        key_and_type, value = line.split("=", 1)
        key, _, _ = key_and_type.partition(":")
        values[key] = value
    return values


def _resolved_presets(project: Path) -> list[dict[str, Any]]:
    preset_file = source_root(project) / "CMakePresets.json"
    if not preset_file.is_file():
        return []
    try:
        raw_presets = json.loads(preset_file.read_text()).get("configurePresets", [])
    except json.JSONDecodeError:
        return []
    by_name = {preset["name"]: preset for preset in raw_presets}

    def resolve(name: str, seen: set[str] | None = None) -> dict[str, Any]:
        seen = seen or set()
        if name in seen:
            return {}
        seen.add(name)
        preset = by_name[name]
        merged: dict[str, Any] = {"cacheVariables": {}}
        parents = preset.get("inherits", [])
        if isinstance(parents, str):
            parents = [parents]
        for parent in parents:
            parent_preset = resolve(parent, seen)
            merged.update({key: value for key, value in parent_preset.items() if key != "cacheVariables"})
            merged["cacheVariables"].update(parent_preset.get("cacheVariables", {}))
        merged.update({key: value for key, value in preset.items() if key != "cacheVariables"})
        merged["cacheVariables"].update(preset.get("cacheVariables", {}))
        return merged

    resolved = []
    for name, preset in by_name.items():
        if not preset.get("hidden"):
            resolved.append(resolve(name))
    return resolved


def _matching_presets(project: Path, directory: Path, cache: dict[str, str]) -> list[str]:
    matches: list[tuple[int, str]] = []
    source_dir = source_root(project)
    for preset in _resolved_presets(project):
        binary_dir = preset.get("binaryDir", "").replace("${sourceDir}", str(source_dir))
        if Path(binary_dir) != directory:
            continue
        variables = preset.get("cacheVariables", {})
        score = 0
        compatible = True
        for key, value in variables.items():
            actual = cache.get(key)
            raw_expected = str(value)
            expected = raw_expected.upper()
            if actual is None or "$env{" in raw_expected:
                continue
            if actual.upper() != expected:
                compatible = False
                break
            score += 1
        if not compatible:
            continue
        matches.append((score, preset["name"]))
    if not matches:
        return []
    best_score = max(score for score, _ in matches)
    return sorted(name for score, name in matches if score == best_score)


def list_builds(project: Path) -> list[BuildDirectory]:
    """Find primary Flexiv CMake build directories and their likely presets."""
    source_dir = source_root(project)
    build_root = source_dir / "build"
    if not build_root.is_dir():
        return []
    builds = []
    for cache_path in sorted(build_root.rglob("CMakeCache.txt")):
        directory = cache_path.parent
        cache = _read_cache(cache_path)
        builds.append(
            BuildDirectory(
                directory=str(directory.relative_to(project)),
                source_directory=cache.get("CMAKE_HOME_DIRECTORY", str(source_dir)),
                build_type=cache.get("CMAKE_BUILD_TYPE"),
                generator=cache.get("CMAKE_GENERATOR"),
                presets=tuple(_matching_presets(project, directory, cache)),
                options=tuple(
                    (label, cache[key])
                    for key, label in {
                        "BuildApp_.All": "apps",
                        "BuildTest_.All": "tests",
                        "BuildTool_.All": "tools",
                        "BuildExample_.All": "examples",
                        "FVR_ENABLE_SANITIZER": "sanitizer",
                    }.items()
                    if key in cache
                ),
            )
        )
    return builds


def select_build(builds: list[BuildDirectory], directory: str | None) -> BuildDirectory:
    """Select a build by path, or display a minimal interactive numbered menu."""
    if not builds:
        raise ProjectError("no configured CMake builds are available")
    if directory:
        requested = Path(directory)
        matches = [build for build in builds if build.directory == directory or Path(build.directory).name == requested.name]
        if len(matches) != 1:
            raise ProjectError(f"no unique configured build matches '{directory}'")
        return matches[0]
    if not sys.stdin.isatty():
        raise ProjectError("specify a build directory when standard input is not interactive")
    print("Select a build directory:")
    for number, build in enumerate(builds, start=1):
        print(f"  {number}) {build.directory} ({build.build_type or 'unknown'})")
    try:
        selected = int(input("Build number: "))
        return builds[selected - 1]
    except (ValueError, EOFError, IndexError) as error:
        raise ProjectError("no valid build directory was selected") from error


def cmake_gui_command(project: Path, build: BuildDirectory) -> list[str]:
    """Return the explicit cmake-gui command for a configured build directory."""
    return ["cmake-gui", "-S", build.source_directory, "-B", str(project / build.directory)]


def open_cmake_gui(project: Path, build: BuildDirectory, dry_run: bool) -> int:
    command = cmake_gui_command(project, build)
    print("$ " + shlex.join(command))
    if dry_run:
        return 0
    if not shutil.which("cmake-gui"):
        raise ProjectError("cmake-gui is not installed or is not on PATH")
    return run_process(command, project, capture_output=False, timeout=None).returncode
