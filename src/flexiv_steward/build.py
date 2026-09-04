"""Repeatable Docker/CMake build and test command construction."""

from __future__ import annotations

import json
import subprocess
import shlex
from dataclasses import dataclass
from pathlib import Path

from flexiv_steward.context import ProjectError
from flexiv_steward.layout import source_relative_path, source_root


@dataclass(frozen=True)
class BuildProfile:
    name: str
    platform: str
    preset: str
    description: str


PROFILES = {
    "all": BuildProfile("all", "ubuntu", "ubuntu-relwithdebinfo-g1-All", "all Ubuntu applications, tests, tools, and examples"),
    "rca": BuildProfile("rca", "ubuntu", "ubuntu-relwithdebinfo-g1-RCA", "RobotControlApp"),
    "ui": BuildProfile("ui", "ubuntu", "ubuntu-relwithdebinfo-g1-UI", "UI application"),
    "debug": BuildProfile("debug", "ubuntu", "ubuntu-debug", "Ubuntu Debug build"),
    "yocto-all": BuildProfile("yocto-all", "yocto", "yocto-relwithdebinfo-g1-All", "all Yocto targets"),
}


def resolve_profile(name: str, preset: str | None = None) -> BuildProfile:
    """Return a named profile or derive one from an explicitly supplied preset."""
    if preset:
        return BuildProfile(name, "yocto" if preset.startswith("yocto-") else "ubuntu", preset, "custom preset")
    try:
        return PROFILES[name]
    except KeyError as error:
        choices = ", ".join(PROFILES)
        raise ProjectError(f"unknown build profile '{name}'; choose one of: {choices}") from error


def _dispatcher(project: Path) -> Path:
    dispatcher = project / "docker" / "docker_dispatch.sh"
    if not dispatcher.is_file():
        raise ProjectError(f"Docker dispatcher is missing: {dispatcher}")
    return dispatcher


def profile_preset(project: Path, profile: BuildProfile) -> str:
    """Return the preset matching *profile* in a wrapper or direct source tree."""
    preset_file = source_root(project) / "CMakePresets.json"
    if not preset_file.is_file():
        return profile.preset
    try:
        available = {
            preset["name"]
            for preset in json.loads(preset_file.read_text()).get("configurePresets", [])
        }
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ProjectError(f"could not read CMake presets: {preset_file}") from error
    if profile.preset in available:
        return profile.preset
    direct_fallbacks = {
        "all": "ubuntu-relwithdebinfo-g1",
        "yocto-all": "yocto-relwithdebinfo-g1",
    }
    fallback = direct_fallbacks.get(profile.name)
    if source_root(project) == project and fallback in available:
        return fallback
    return profile.preset


def _source_shell_command(project: Path, command: str) -> str:
    relative = source_relative_path(project)
    if relative == Path("."):
        return command
    return f"cd {shlex.quote(str(relative))} && {command}"


def configure_command(project: Path, profile: BuildProfile) -> list[str]:
    """Build the command that configures a CMake preset in the Docker harness."""
    _dispatcher(project)
    preset = profile_preset(project, profile)
    return [
        "bash",
        "docker/docker_dispatch.sh",
        "exec",
        profile.platform,
        "bash",
        "-c",
        _source_shell_command(project, f"cmake --preset {shlex.quote(preset)}"),
    ]


def build_command(project: Path, profile: BuildProfile, targets: list[str]) -> list[str]:
    """Build an incremental CMake preset in the Docker execution container."""
    _dispatcher(project)
    preset = profile_preset(project, profile)
    target_args = " ".join(f"--target {shlex.quote(target)}" for target in targets)
    return [
        "bash",
        "docker/docker_dispatch.sh",
        "exec",
        profile.platform,
        "bash",
        "-c",
        _source_shell_command(
            project,
            f"cmake --build --preset {shlex.quote(preset)} {target_args}".rstrip(),
        ),
    ]


def workflow_test_command(project: Path) -> list[str]:
    """Return the project-local workflow integration test invocation."""
    script = source_root(project) / "tool" / "WorkflowTest" / "run_workflow_test.sh"
    if not script.is_file():
        raise ProjectError(f"workflow test runner is missing: {script}")
    return ["bash", str(script)]


def run_command(command: list[str], project: Path, dry_run: bool) -> int:
    """Echo a reproducible command and execute it only when requested."""
    print("$ " + shlex.join(command))
    if dry_run:
        return 0
    return subprocess.run(command, cwd=project, check=False).returncode
