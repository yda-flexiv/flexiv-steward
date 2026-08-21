"""Bootstrap a new Flexiv worktree into a reproducible build-ready state."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

from flexiv_steward.build import BuildProfile, configure_command, run_command
from flexiv_steward.context import ProjectError
from flexiv_steward.deps import dependency_doctor, format_dependency_doctor, has_dependency_errors


def submodule_command() -> list[str]:
    return ["git", "submodule", "update", "--init", "--recursive"]


def compile_commands_source(project: Path, preset_name: str) -> Path:
    """Return the compile database path configured by *preset_name*."""
    preset_file = project / "flexiv" / "CMakePresets.json"
    try:
        presets = json.loads(preset_file.read_text()).get("configurePresets", [])
    except (OSError, json.JSONDecodeError) as error:
        raise ProjectError(f"could not read CMake presets: {preset_file}") from error
    by_name = {preset.get("name"): preset for preset in presets}

    def binary_dir(name: str, seen: set[str] | None = None) -> str | None:
        seen = seen or set()
        if name in seen:
            raise ProjectError(f"cyclic CMake preset inheritance involving '{name}'")
        preset = by_name.get(name)
        if not preset:
            raise ProjectError(f"CMake configure preset '{name}' was not found in {preset_file}")
        if value := preset.get("binaryDir"):
            return value
        inherited = preset.get("inherits", [])
        if isinstance(inherited, str):
            inherited = [inherited]
        for parent in inherited:
            if value := binary_dir(parent, seen | {name}):
                return value
        return None

    configured_dir = binary_dir(preset_name)
    if not configured_dir:
        raise ProjectError(f"CMake configure preset '{preset_name}' has no binaryDir")
    source_dir = project / "flexiv"
    return Path(configured_dir.replace("${sourceDir}", str(source_dir))) / "compile_commands.json"


def configure_editor_files(project: Path, preset_name: str) -> None:
    """Expose CMake's compile database for clangd and mark the worktree for Projectile."""
    source = compile_commands_source(project, preset_name)
    if not source.is_file():
        raise ProjectError(f"compile database was not generated: {source}")
    rewrite = project / "docker" / "rewrite_compile_commands_for_clangd.sh"
    if not rewrite.is_file():
        raise ProjectError(f"clangd database rewriter is missing: {rewrite}")
    environment = dict(os.environ, FLEXIV_CLANGD_DB_IN=str(source))
    result = subprocess.run(["bash", str(rewrite)], cwd=project, env=environment, check=False)
    if result.returncode:
        raise ProjectError("failed to rewrite compile database for clangd")
    host_database = project / "flexiv" / "compile_commands.json"
    if not host_database.is_file():
        raise ProjectError(f"clangd database was not produced: {host_database}")
    destination = project / "compile_commands.json"
    if destination.is_symlink():
        destination.unlink()
    elif destination.exists():
        raise ProjectError(f"refusing to replace existing file: {destination}")
    destination.symlink_to("flexiv/compile_commands.json")
    (project / ".projectile").touch(exist_ok=True)
    print(f"Linked compile_commands.json to {host_database}.")
    print("Created .projectile.")


def setup_worktree(project: Path, profile: BuildProfile, dry_run: bool) -> int:
    """Initialize submodules, configure external repositories, then verify versions."""
    if dry_run:
        configure = [
            "bash",
            "docker/docker_dispatch.sh",
            "exec",
            profile.platform,
            "bash",
            "-c",
            f"cd flexiv && cmake --preset {shlex.quote(profile.preset)}",
        ]
        for command in [submodule_command(), configure]:
            print("$ " + shlex.join(command))
        print("$ FLEXIV_CLANGD_DB_IN=<preset-build>/compile_commands.json bash docker/rewrite_compile_commands_for_clangd.sh")
        print("$ ln -s flexiv/compile_commands.json compile_commands.json")
        print("$ touch .projectile")
        print("$ fs deps doctor --strict")
        return 0
    print("Initializing recursive Git submodules…")
    result = subprocess.run(submodule_command(), cwd=project, check=False).returncode
    if result:
        return result
    # The dispatcher itself lives in the docker submodule, so construct the
    # configure command only after recursive submodules have been populated.
    configure = configure_command(project, profile)
    print(f"Configuring {profile.name} ({profile.preset})…")
    result = run_command(configure, project, dry_run=False)
    if result:
        return result
    configure_editor_files(project, profile.preset)
    statuses = dependency_doctor(project)
    print(format_dependency_doctor(statuses))
    if has_dependency_errors(statuses):
        raise ProjectError("setup completed with dependency drift; resolve it before building")
    return 0
