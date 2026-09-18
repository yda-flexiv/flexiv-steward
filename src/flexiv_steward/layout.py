"""Resolve Flexiv source paths across wrapper and direct repositories."""

from __future__ import annotations

from pathlib import Path


def source_root(project: Path) -> Path:
    """Return the CMake source tree for a wrapper or direct worktree."""
    nested = project / "flexiv"
    if nested.is_dir():
        return nested
    return project


def source_relative_path(project: Path) -> Path:
    """Return the source tree path relative to the Git worktree root."""
    return source_root(project).relative_to(project)


def bare_workspace(git_dir: Path) -> Path:
    """Return the workspace root for a bare Git directory."""
    return git_dir.parent if git_dir.name == ".git" else git_dir
