"""Project discovery and read-only Flexiv workspace context."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TypedDict, cast

from flexiv_steward.errors import ProjectError
from flexiv_steward.layout import source_root
from flexiv_steward.models import (
    CMakeContext,
    CompileCommandsContext,
    DockerContext,
    NestedRepository,
    ProjectContext,
    RepositoryStatus,
    SubmoduleContext,
)
from flexiv_steward.process import run_git


class _Preset(TypedDict, total=False):
    name: str
    hidden: bool


class _PresetFile(TypedDict, total=False):
    configurePresets: list[_Preset]


def discover_project(path: str | Path | None = None) -> Path:
    """Return the top-level Git worktree containing *path*."""
    candidate = Path(path or Path.cwd()).expanduser().resolve()
    return Path(run_git(candidate, "rev-parse", "--show-toplevel")).resolve()


def _status(project: Path) -> RepositoryStatus:
    porcelain = run_git(project, "status", "--porcelain=v1", "--branch")
    branch: str | None = None
    upstream: str | None = None
    ahead = 0
    behind = 0
    changes: list[str] = []
    for line in porcelain.splitlines():
        if line.startswith("## "):
            branch_line = line[3:]
            branch, _, tracking = branch_line.partition("...")
            if tracking:
                upstream, _, tracking_counts = tracking.partition(" ")
                if "ahead " in tracking_counts:
                    ahead = int(tracking_counts.split("ahead ", 1)[1].split("]", 1)[0].split(",", 1)[0])
                if "behind " in tracking_counts:
                    behind = int(tracking_counts.split("behind ", 1)[1].split("]", 1)[0].split(",", 1)[0])
        elif line:
            changes.append(line)
    push_target = upstream
    unpushed = ahead
    if branch:
        remote_branches = run_git(
            project, "for-each-ref", "--format=%(refname:short)", "refs/remotes"
        ).splitlines()
        candidates = [ref for ref in remote_branches if ref.endswith(f"/{branch}")]
        if candidates:
            push_target = next((ref for ref in candidates if ref.startswith("origin/")), candidates[0])
            divergence = run_git(project, "rev-list", "--left-right", "--count", f"{push_target}...HEAD").split()
            unpushed = int(divergence[1])
    return RepositoryStatus(
        branch or "HEAD detached", upstream, ahead, behind, push_target, unpushed, tuple(changes)
    )


def _submodules(project: Path) -> tuple[SubmoduleContext, ...]:
    modules: list[SubmoduleContext] = []
    for line in run_git(project, "submodule", "status", "--recursive").splitlines():
        if not line:
            continue
        marker = line[0]
        parts = line[1:].strip().split(maxsplit=2)
        if len(parts) < 2:
            continue
        try:
            status = _status(project / parts[1])
        except ProjectError:
            status = None
        modules.append(
            SubmoduleContext(
                {"-": "uninitialized", "+": "modified", "U": "conflicted"}.get(marker, "ok"),
                parts[0],
                parts[1],
                parts[2] if len(parts) == 3 else "",
                status,
            )
        )
    return tuple(modules)


def _presets(project: Path) -> tuple[str, ...]:
    preset_file = source_root(project) / "CMakePresets.json"
    if not preset_file.is_file():
        return ()
    try:
        data = cast(_PresetFile, json.loads(preset_file.read_text()))
    except (json.JSONDecodeError, OSError):
        return ()
    return tuple(
        preset["name"]
        for preset in data.get("configurePresets", [])
        if "name" in preset and not preset.get("hidden", False)
    )


def _nested_repo(path: Path, name: str) -> NestedRepository | None:
    if not path.is_dir():
        return None
    try:
        status = _status(path)
        return NestedRepository(
            name, path, run_git(path, "rev-parse", "--short", "HEAD"), status.branch, status
        )
    except ProjectError:
        return None


def collect_context(path: str | Path | None = None) -> ProjectContext:
    """Collect a compact, read-only snapshot of a Flexiv worktree."""
    project = discover_project(path)
    source_dir = source_root(project)
    compile_database = project / "compile_commands.json"
    nested = (
        _nested_repo(source_dir / "external" / "flexiv_sw_base", "flexiv_sw_base")
        if source_dir != project
        else None
    )
    return ProjectContext(
        project=project,
        head=run_git(project, "rev-parse", "--short", "HEAD"),
        status=_status(project),
        submodules=_submodules(project),
        nested_repositories=(nested,) if nested else (),
        cmake=CMakeContext(
            source_dir / "CMakePresets.json",
            _presets(project),
            CompileCommandsContext(compile_database, compile_database.exists()),
        ),
        docker=DockerContext(
            project / "docker" / "docker_dispatch.sh",
            (project / "docker" / "docker_dispatch.sh").is_file(),
            shutil.which("docker") is not None,
        ),
    )


__all__ = ["ProjectError", "collect_context", "discover_project", "run_git"]
