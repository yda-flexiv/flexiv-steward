"""Repeatable Docker/CMake build and test command construction."""

from __future__ import annotations

import subprocess
import shlex
from dataclasses import dataclass
from pathlib import Path

from flexiv_steward.context import ProjectError


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


def configure_command(project: Path, profile: BuildProfile) -> list[str]:
    """Build the command that configures a CMake preset in the Docker harness."""
    _dispatcher(project)
    return [
        "bash",
        "docker/docker_dispatch.sh",
        "exec",
        profile.platform,
        "bash",
        "-c",
        f"cd flexiv && cmake --preset {shlex.quote(profile.preset)}",
    ]


def build_command(project: Path, profile: BuildProfile, targets: list[str]) -> list[str]:
    """Build an incremental CMake preset in the Docker execution container."""
    _dispatcher(project)
    target_args = " ".join(f"--target {shlex.quote(target)}" for target in targets)
    return [
        "bash",
        "docker/docker_dispatch.sh",
        "exec",
        profile.platform,
        "bash",
        "-c",
        f"cd flexiv && cmake --build --preset {shlex.quote(profile.preset)} {target_args}".rstrip(),
    ]


def workflow_test_command(project: Path) -> list[str]:
    """Return the project-local workflow integration test invocation."""
    script = project / "flexiv" / "tool" / "WorkflowTest" / "run_workflow_test.sh"
    if not script.is_file():
        raise ProjectError(f"workflow test runner is missing: {script}")
    return ["bash", str(script)]


def run_command(command: list[str], project: Path, dry_run: bool) -> int:
    """Echo a reproducible command and execute it only when requested."""
    print("$ " + shlex.join(command))
    if dry_run:
        return 0
    return subprocess.run(command, cwd=project, check=False).returncode
