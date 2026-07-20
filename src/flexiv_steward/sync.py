"""Safe synchronization of a named Git worktree with its upstream base branch."""

from __future__ import annotations

import subprocess
import shlex
from dataclasses import dataclass
from pathlib import Path

from flexiv_steward.context import ProjectError, run_git
from flexiv_steward.worktree import inspect_worktrees


@dataclass(frozen=True)
class SyncResult:
    """The terminal state of one fetch-and-merge synchronization attempt."""

    state: str
    worktree: Path
    base: str
    conflicts: list[str]
    output: str


def resolve_worktree(project: Path, identifier: str) -> Path:
    """Resolve a worktree absolute path or its unique directory basename."""
    candidates = inspect_worktrees(project)
    requested = Path(identifier).expanduser()
    if requested.is_absolute():
        resolved = requested.resolve()
        matches = [entry for entry in candidates if Path(entry["path"]).resolve() == resolved]
    else:
        matches = [entry for entry in candidates if Path(entry["path"]).name == identifier]
    if not matches:
        raise ProjectError(f"no worktree named '{identifier}'")
    if len(matches) > 1:
        raise ProjectError(f"worktree name '{identifier}' is ambiguous; provide its absolute path")
    return Path(matches[0]["path"])


def _run(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=project, check=False, text=True, capture_output=True)


def _fetch_command(base: str) -> list[str]:
    remote, separator, branch = base.partition("/")
    if separator:
        return ["fetch", remote, branch]
    return ["fetch", "origin", base]


def _instructions(worktree: Path) -> str:
    return "\n".join(
        [
            "Resolve the conflicted files, then complete the merge:",
            f"  cd {shlex.quote(str(worktree))}",
            "  git add <resolved-files>",
            "  git commit",
            "Or abandon this synchronization attempt:",
            "  git merge --abort",
        ]
    )


def sync_worktree(project: Path, identifier: str, base: str, dry_run: bool) -> SyncResult:
    """Fetch *base* then merge it, leaving conflicts untouched for the user."""
    worktree = resolve_worktree(project, identifier)
    if run_git(worktree, "diff", "--name-only", "--diff-filter=U"):
        return SyncResult("merge-in-progress", worktree, base, [], "An unresolved merge already exists in this worktree.")
    changes = run_git(worktree, "status", "--porcelain")
    if changes:
        return SyncResult("dirty", worktree, base, [], "Refusing to merge into a worktree with local changes.")
    fetch = _fetch_command(base)
    commands = [shlex.join(["git", *fetch]), shlex.join(["git", "merge", "--no-edit", base])]
    if dry_run:
        return SyncResult("dry-run", worktree, base, [], "\n".join(commands))
    fetch_result = _run(worktree, *fetch)
    if fetch_result.returncode:
        return SyncResult("fetch-failed", worktree, base, [], (fetch_result.stdout + fetch_result.stderr).strip())
    merge_result = _run(worktree, "merge", "--no-edit", base)
    output = (merge_result.stdout + merge_result.stderr).strip()
    if merge_result.returncode == 0:
        return SyncResult("synced", worktree, base, [], output)
    conflicts = run_git(worktree, "diff", "--name-only", "--diff-filter=U").splitlines()
    if conflicts:
        return SyncResult("conflict", worktree, base, conflicts, output)
    return SyncResult("merge-failed", worktree, base, [], output)


def format_sync(result: SyncResult) -> str:
    """Render sync outcome with instructions only when user intervention is needed."""
    lines = [f"Worktree: {result.worktree}", f"Base: {result.base}", f"Result: {result.state}"]
    if result.output:
        lines.extend(["", result.output])
    if result.conflicts:
        lines.extend(["", "Conflicted files:", *(f"  {path}" for path in result.conflicts), "", _instructions(result.worktree)])
    elif result.state in {"dirty", "merge-in-progress"}:
        lines.extend(["", "Commit, stash, or discard local work before retrying."])
    return "\n".join(lines)
