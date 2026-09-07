"""Safe synchronization of a named Git worktree with its upstream base branch."""

from __future__ import annotations

import shlex
from pathlib import Path

from flexiv_steward.errors import ProjectError
from flexiv_steward.models import SyncResult
from flexiv_steward.process import run_git, run_process
from flexiv_steward.worktree import inspect_worktrees


def resolve_worktree(project: Path, identifier: str) -> Path:
    """Resolve a worktree absolute path or its unique directory basename."""
    candidates = inspect_worktrees(project)
    requested = Path(identifier).expanduser()
    if requested.is_absolute():
        resolved = requested.resolve()
        matches = [entry for entry in candidates if entry.path.resolve() == resolved]
    else:
        matches = [entry for entry in candidates if entry.path.name == identifier]
    if not matches:
        raise ProjectError(f"no worktree named '{identifier}'")
    if len(matches) > 1:
        raise ProjectError(f"worktree name '{identifier}' is ambiguous; provide its absolute path")
    return matches[0].path


def _fetch_command(base: str) -> list[str]:
    remote, separator, branch = base.partition("/")
    if separator:
        return ["fetch", remote, branch]
    return ["fetch", "origin", base]


def sync_worktree(project: Path, identifier: str, base: str, dry_run: bool) -> SyncResult:
    """Fetch *base* then merge it, leaving conflicts untouched for the user."""
    worktree = resolve_worktree(project, identifier)
    if run_git(worktree, "diff", "--name-only", "--diff-filter=U"):
        return SyncResult("merge-in-progress", worktree, base, (), "An unresolved merge already exists in this worktree.")
    changes = run_git(worktree, "status", "--porcelain")
    if changes:
        return SyncResult("dirty", worktree, base, (), "Refusing to merge into a worktree with local changes.")
    fetch = _fetch_command(base)
    commands = [shlex.join(["git", *fetch]), shlex.join(["git", "merge", "--no-edit", base])]
    if dry_run:
        return SyncResult("dry-run", worktree, base, (), "\n".join(commands))
    fetch_result = run_process(["git", *fetch], worktree)
    if fetch_result.returncode:
        return SyncResult("fetch-failed", worktree, base, (), fetch_result.output)
    merge_result = run_process(["git", "merge", "--no-edit", base], worktree)
    output = merge_result.output
    if merge_result.returncode == 0:
        return SyncResult("synced", worktree, base, (), output)
    conflicts = run_git(worktree, "diff", "--name-only", "--diff-filter=U").splitlines()
    if conflicts:
        return SyncResult("conflict", worktree, base, tuple(conflicts), output)
    return SyncResult("merge-failed", worktree, base, (), output)
