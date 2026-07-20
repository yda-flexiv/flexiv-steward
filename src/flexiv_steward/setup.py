"""Bootstrap a new Flexiv worktree into a reproducible build-ready state."""

from __future__ import annotations

import subprocess
from pathlib import Path

from flexiv_steward.build import BuildProfile, configure_command, run_command
from flexiv_steward.context import ProjectError
from flexiv_steward.deps import dependency_doctor, format_dependency_doctor, has_dependency_errors


def submodule_command() -> list[str]:
    return ["git", "submodule", "update", "--init", "--recursive"]


def setup_worktree(project: Path, profile: BuildProfile, dry_run: bool) -> int:
    """Initialize submodules, configure external repositories, then verify versions."""
    configure = [
        "bash",
        "docker/docker_dispatch.sh",
        "exec",
        profile.platform,
        "bash",
        "-c",
        f"cd flexiv && cmake --preset {profile.preset}",
    ]
    if dry_run:
        for command in [submodule_command(), configure]:
            print("$ " + " ".join(command))
        print("$ fs deps doctor --strict")
        return 0
    print("Initializing recursive Git submodules…")
    result = subprocess.run(submodule_command(), cwd=project, check=False).returncode
    if result:
        return result
    print(f"Configuring {profile.name} ({profile.preset})…")
    result = run_command(configure_command(project, profile), project, dry_run=False)
    if result:
        return result
    statuses = dependency_doctor(project)
    print(format_dependency_doctor(statuses))
    if has_dependency_errors(statuses):
        raise ProjectError("setup completed with dependency drift; resolve it before building")
    return 0
