"""Bootstrap a new Flexiv worktree into a reproducible build-ready state."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path

from flexiv_steward.build import BuildProfile, configure_command, profile_preset, run_command
from flexiv_steward.context import ProjectError
from flexiv_steward.deps import dependency_doctor, format_dependency_doctor, has_dependency_errors
from flexiv_steward.layout import source_root


def submodule_command() -> list[str]:
    return ["git", "submodule", "update", "--init", "--recursive"]


def _git_output(project: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=project, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        return ""
    return result.stdout


def _submodule_cache_root(project: Path) -> Path | None:
    """Return a cache that survives linked-worktree removal."""
    output = _git_output(project, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    return Path(output) / "flexiv-steward" / "submodules" if output else None


def _submodule_cache_path(cache_root: Path, relative_path: Path) -> Path:
    digest = hashlib.sha256(relative_path.as_posix().encode()).hexdigest()[:16]
    return cache_root / f"{digest}-{relative_path.name}.git"


def _is_git_object_cache(path: Path) -> bool:
    return (path / "HEAD").is_file() and (path / "objects").is_dir()


def _submodule_paths(project: Path) -> list[Path]:
    """Read submodule paths without requiring their checkouts to exist."""
    output = _git_output(project, "config", "--file=.gitmodules", "--get-regexp", r"^submodule\..*\.path$")
    paths: list[Path] = []
    for line in output.splitlines():
        _, path = line.split(None, 1)
        paths.append(Path(path))
    return paths


def _submodule_update_commands(project: Path) -> list[tuple[Path, list[str]]]:
    """Build cache-aware update commands for this worktree and existing children."""
    commands: list[tuple[Path, list[str]]] = [(project, ["git", "submodule", "init"])]
    cache_root = _submodule_cache_root(project)
    pending = [(project, Path())]
    while pending:
        parent, relative_parent = pending.pop(0)
        for path in _submodule_paths(parent):
            checkout = parent / path
            relative_path = relative_parent / path
            reference = _submodule_cache_path(cache_root, relative_path) if cache_root else None
            command = ["git", "submodule", "update", "--init"]
            if reference is not None and _is_git_object_cache(reference):
                command.extend(["--reference", str(reference)])
            command.append(str(path))
            commands.append((parent, command))
            if checkout.is_dir():
                commands.append((checkout, ["git", "submodule", "init"]))
                pending.append((checkout, relative_path))
    return commands


def _update_submodule_cache(checkout: Path, cache: Path) -> int:
    """Persist objects needed by *checkout* independently of any worktree."""
    commit = _git_output(checkout, "rev-parse", "HEAD").strip()
    if not commit:
        return 0
    cache.parent.mkdir(parents=True, exist_ok=True)
    reference = f"refs/flexiv-steward/{commit}"
    if not _is_git_object_cache(cache):
        result = subprocess.run(
            ["git", "clone", "--mirror", "--no-hardlinks", str(checkout), str(cache)],
            check=False,
        )
        if result.returncode:
            return result.returncode
        origin = _git_output(checkout, "remote", "get-url", "origin").strip()
        if origin:
            subprocess.run(["git", "remote", "set-url", "origin", origin], cwd=cache, check=False)
        return subprocess.run(["git", "update-ref", reference, commit], cwd=cache, check=False).returncode
    return subprocess.run(
        ["git", "fetch", "--no-tags", str(checkout), f"+HEAD:{reference}"],
        cwd=cache,
        check=False,
    ).returncode


def _initialize_submodules(project: Path, dry_run: bool) -> int:
    """Initialize submodules using the superproject's persistent object cache."""
    if not (project / ".gitmodules").is_file():
        command = submodule_command()
        if dry_run:
            print("$ " + shlex.join(command))
            return 0
        return subprocess.run(command, cwd=project, check=False).returncode

    if dry_run:
        commands = _submodule_update_commands(project)
        for cwd, command in commands:
            prefix = "" if cwd == project else f"(cd {shlex.quote(str(cwd))} && "
            suffix = "" if cwd == project else ")"
            print(f"$ {prefix}{shlex.join(command)}{suffix}")
        return 0
    result = subprocess.run(["git", "submodule", "init"], cwd=project, check=False)
    if result.returncode:
        return result.returncode
    cache_root = _submodule_cache_root(project)
    pending = [(project, Path())]
    while pending:
        parent, relative_parent = pending.pop(0)
        for path in _submodule_paths(parent):
            checkout = parent / path
            relative_path = relative_parent / path
            reference = _submodule_cache_path(cache_root, relative_path) if cache_root else None
            command = ["git", "submodule", "update", "--init"]
            if reference is not None and _is_git_object_cache(reference):
                command.extend(["--reference", str(reference)])
            command.append(str(path))
            result = subprocess.run(command, cwd=parent, check=False)
            if result.returncode:
                return result.returncode
            if checkout.is_dir():
                if reference is not None:
                    result = _update_submodule_cache(checkout, reference)
                    if result:
                        return result
                result = subprocess.run(["git", "submodule", "init"], cwd=checkout, check=False)
                if result.returncode:
                    return result.returncode
                pending.append((checkout, relative_path))
    return 0


def compile_commands_source(project: Path, preset_name: str) -> Path:
    """Return the compile database path configured by *preset_name*."""
    source_dir = source_root(project)
    preset_file = source_dir / "CMakePresets.json"
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
    return Path(configured_dir.replace("${sourceDir}", str(source_dir))) / "compile_commands.json"


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
    environment = dict(
        os.environ,
        FLEXIV_CLANGD_DB_IN=str(source),
        FLEXIV_CLANGD_DB_OUT=str(host_database),
    )
    result = subprocess.run(["bash", str(rewrite)], cwd=project, env=environment, check=False)
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


def setup_worktree(project: Path, profile: BuildProfile, dry_run: bool) -> int:
    """Initialize submodules, configure external repositories, then verify versions."""
    if dry_run:
        preset = profile_preset(project, profile)
        source_dir = source_root(project)
        source_prefix = "" if source_dir == project else f"cd {shlex.quote(str(source_dir.relative_to(project)))} && "
        configure = [
            "bash",
            "docker/docker_dispatch.sh",
            "exec",
            profile.platform,
            "bash",
            "-c",
            f"{source_prefix}cmake --preset {shlex.quote(preset)}",
        ]
        _initialize_submodules(project, dry_run=True)
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
    # The dispatcher itself lives in the docker submodule, so construct the
    # configure command only after recursive submodules have been populated.
    preset = profile_preset(project, profile)
    configure = configure_command(project, profile)
    print(f"Configuring {profile.name} ({preset})…")
    result = run_command(configure, project, dry_run=False)
    if result:
        return result
    configure_editor_files(project, preset)
    statuses = dependency_doctor(project)
    print(format_dependency_doctor(statuses))
    if has_dependency_errors(statuses):
        raise ProjectError("setup completed with dependency drift; resolve it before building")
    return 0
