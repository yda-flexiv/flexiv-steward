"""Repeatable Docker/CMake build and test command construction."""

from __future__ import annotations

import shlex
from pathlib import Path

from flexiv_steward.errors import ProjectError
from flexiv_steward.layout import source_relative_path, source_root
from flexiv_steward.presets import Preset
from flexiv_steward.process import run_process


def _dispatcher(project: Path) -> Path:
    dispatcher = project / "docker" / "docker_dispatch.sh"
    if not dispatcher.is_file():
        raise ProjectError(f"Docker dispatcher is missing: {dispatcher}")
    return dispatcher


def _source_shell_command(project: Path, command: str) -> str:
    relative = source_relative_path(project)
    if relative == Path("."):
        return command
    return f"cd {shlex.quote(str(relative))} && {command}"


def configure_command(project: Path, preset: Preset) -> list[str]:
    """Configure the preset named by a build or setup request in Docker."""
    _dispatcher(project)
    return [
        "bash", "docker/docker_dispatch.sh", "exec", preset.platform, "bash", "-c",
        _source_shell_command(project, f"cmake --preset {shlex.quote(preset.configure_name)}"),
    ]


def build_command(project: Path, preset: Preset, targets: list[str]) -> list[str]:
    """Build an incremental CMake build preset in Docker."""
    _dispatcher(project)
    target_args = " ".join(f"--target {shlex.quote(target)}" for target in targets)
    return [
        "bash", "docker/docker_dispatch.sh", "exec", preset.platform, "bash", "-c",
        _source_shell_command(
            project, f"cmake --build --preset {shlex.quote(preset.name)} {target_args}".rstrip()
        ),
    ]


def workflow_test_command(project: Path) -> list[str]:
    """Return the project-local workflow integration test invocation."""
    script = source_root(project) / "tool" / "WorkflowTest" / "run_workflow_test.sh"
    if not script.is_file():
        raise ProjectError(f"workflow test runner is missing: {script}")
    return ["bash", str(script)]


def run_command(command: list[str], project: Path, dry_run: bool) -> int:
    """Echo a reproducible command and execute only when requested."""
    print("$ " + shlex.join(command))
    if dry_run:
        return 0
    return run_process(command, project, capture_output=False, timeout=None).returncode
