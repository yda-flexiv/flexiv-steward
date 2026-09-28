"""Bootstrap a new Flexiv worktree into a reproducible build-ready state."""

from __future__ import annotations

import shlex
from pathlib import Path

from flexiv_steward.build import configure_command, run_command
from flexiv_steward.deps import dependency_doctor, has_dependency_errors
from flexiv_steward.errors import ProjectError
from flexiv_steward.externals import initialize_externals
from flexiv_steward.formatters import format_dependency_doctor
from flexiv_steward.layout import source_root
from flexiv_steward.process import merged_environment, run_process
from flexiv_steward.presets import Preset, compile_commands_path
from flexiv_steward.submodules import initialize_submodules


def _initialize_submodules(project: Path, dry_run: bool) -> int:
    """Initialize submodules using the superproject's persistent object cache."""
    return initialize_submodules(project, dry_run)


def compile_commands_source(project: Path, preset_name: str) -> Path:
    """Return the compile database path configured by *preset_name*."""
    return compile_commands_path(project, preset_name)


def configure_editor_files(project: Path, preset_name: str) -> None:
    """Expose CMake's compile database for clangd and mark the worktree for Projectile."""
    source = compile_commands_source(project, preset_name)
    if not source.is_file():
        raise ProjectError(f"compile database was not generated: {source}")
    rewrite = project / "docker" / "rewrite_compile_commands_for_clangd.sh"
    if not rewrite.is_file():
        raise ProjectError(f"clangd database rewriter is missing: {rewrite}")
    source_dir = source_root(project)
    host_database = source_dir / "compile_commands.json"
    environment = merged_environment(
        FLEXIV_CLANGD_DB_IN=str(source),
        FLEXIV_CLANGD_DB_OUT=str(host_database),
    )
    result = run_process(["bash", str(rewrite)], project, env=environment, capture_output=False)
    if result.returncode:
        raise ProjectError("failed to rewrite compile database for clangd")
    if not host_database.is_file():
        raise ProjectError(f"clangd database was not produced: {host_database}")
    destination = project / "compile_commands.json"
    if destination != host_database:
        if destination.is_symlink():
            destination.unlink()
        elif destination.exists():
            raise ProjectError(f"refusing to replace existing file: {destination}")
        destination.symlink_to(host_database.relative_to(project))
    (project / ".projectile").touch(exist_ok=True)
    action = (
        "Linked compile_commands.json to"
        if destination != host_database
        else "Prepared compile_commands.json at"
    )
    print(f"{action} {host_database}.")
    print("Created .projectile.")


def setup_worktree(project: Path, preset: Preset, dry_run: bool) -> int:
    """Initialize submodules, configure external repositories, then verify versions."""
    if dry_run:
        source_dir = source_root(project)
        source_prefix = "" if source_dir == project else f"cd {shlex.quote(str(source_dir.relative_to(project)))} && "
        configure = [
            "bash",
            "docker/docker_dispatch.sh",
            "exec",
            preset.platform,
            "bash",
            "-c",
            f"{source_prefix}cmake --preset {shlex.quote(preset.name)}",
        ]
        _initialize_submodules(project, dry_run=True)
        initialize_externals(project, dry_run=True)
        print("$ " + shlex.join(configure))
        print(
            "$ FLEXIV_CLANGD_DB_IN=<preset-build>/compile_commands.json "
            f"FLEXIV_CLANGD_DB_OUT={source_dir / 'compile_commands.json'} "
            "bash docker/rewrite_compile_commands_for_clangd.sh"
        )
        if source_dir != project:
            print(f"$ ln -s {source_dir.relative_to(project) / 'compile_commands.json'} compile_commands.json")
        print("$ touch .projectile")
        print("$ fs deps doctor --strict")
        return 0
    print("Initializing recursive Git submodules…")
    result = _initialize_submodules(project, dry_run=False)
    if result:
        return result
    print("Initializing Flexiv external repositories from cache…")
    initialize_externals(project, dry_run=False)
    # The dispatcher itself lives in the docker submodule, so construct the
    # configure command only after recursive submodules have been populated.
    configure = configure_command(project, preset)
    print(f"Configuring {preset.name}…")
    result = run_command(configure, project, dry_run=False)
    if result:
        return result
    configure_editor_files(project, preset.name)
    statuses = dependency_doctor(project)
    print(format_dependency_doctor(statuses))
    if has_dependency_errors(statuses):
        raise ProjectError("setup completed with dependency drift; resolve it before building")
    return 0
