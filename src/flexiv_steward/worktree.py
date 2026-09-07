"""Git worktree operations and non-mutating health checks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from flexiv_steward.errors import ProjectError
from flexiv_steward.models import WorktreeInfo
from flexiv_steward.process import run_git, run_process


class _RawWorktree(TypedDict, total=False):
    path: str
    head: str
    branch: str | None
    detached: bool
    locked: str | bool
    prunable: str | bool
    bare: bool


def parse_worktree_porcelain(output: str) -> tuple[WorktreeInfo, ...]:
    """Parse Git's porcelain worktree listing."""
    worktrees: list[WorktreeInfo] = []
    current: _RawWorktree = {}
    for line in [*output.splitlines(), ""]:
        if not line:
            if current and "path" in current and not current.get("bare", False):
                worktrees.append(
                    WorktreeInfo(
                        path=Path(current["path"]),
                        head=current.get("head", ""),
                        branch=current.get("branch"),
                        detached=current.get("detached", False),
                        locked=current.get("locked", False),
                        prunable=current.get("prunable", False),
                    )
                )
            current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached":
            current["detached"] = True
        elif key == "locked":
            current["locked"] = value or True
        elif key == "prunable":
            current["prunable"] = value or True
        elif key == "bare":
            current["bare"] = True
    return tuple(worktrees)


def _porcelain_paths(output: str) -> list[str]:
    """Return one current path per NUL-delimited porcelain status entry."""
    paths: list[str] = []
    records = output.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        if len(record) < 4:
            index += 1
            continue
        state = record[:2]
        paths.append(record[3:])
        index += 2 if "R" in state or "C" in state else 1
    return paths


def _last_activity(path: Path, status_output: str) -> tuple[str | None, str | None]:
    """Return the latest Git or changed-file activity as UTC ISO time."""
    candidates: list[tuple[float, str]] = []
    try:
        reflog_time = run_git(path, "reflog", "show", "-1", "--format=%ct", "HEAD")
        if reflog_time:
            candidates.append((float(reflog_time), "reflog"))
    except (ProjectError, ValueError):
        pass
    for relative_path in _porcelain_paths(status_output):
        try:
            candidates.append(((path / relative_path).stat().st_mtime, "working-tree"))
        except OSError:
            pass
    if not candidates:
        try:
            candidates.append((path.stat().st_mtime, "directory"))
        except OSError:
            return None, None
    timestamp, source = max(candidates)
    activity = datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")
    return activity, source


def _inspect_worktree(worktree: WorktreeInfo) -> WorktreeInfo:
    """Add status and activity metadata to one parsed worktree."""
    path = worktree.path
    exists = path.is_dir()
    changes: int | None = None
    activity: str | None = None
    source: str | None = None
    if exists and not worktree.prunable:
        status_output = ""
        try:
            status_output = run_git(path, "status", "--porcelain=v1", "-z")
            changes = len(_porcelain_paths(status_output))
        except ProjectError:
            pass
        activity, source = _last_activity(path, status_output)
    issues: list[str] = []
    if worktree.prunable or not exists:
        issues.append("prunable")
    if worktree.locked:
        issues.append("locked")
    if changes:
        issues.append("dirty")
    return WorktreeInfo(
        path=path,
        head=worktree.head,
        branch=worktree.branch,
        detached=worktree.detached,
        locked=worktree.locked,
        prunable=worktree.prunable,
        exists=exists,
        changes=changes,
        last_activity=activity,
        last_activity_source=source,
        issues=tuple(issues),
    )


def inspect_worktrees(project: str | Path) -> tuple[WorktreeInfo, ...]:
    """Return status and safety signals for every worktree in the repository."""
    project_path = Path(project)
    worktrees = parse_worktree_porcelain(run_git(project_path, "worktree", "list", "--porcelain"))
    worker_count = min(8, len(worktrees))
    if not worker_count:
        return ()
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return tuple(executor.map(_inspect_worktree, worktrees))

def create_worktree(worktree_path: str, branch_name: str) -> None:
    """
    Create a new git worktree at the specified path and checkout the given branch.

    Args:
        worktree_path (str): The path where the new worktree will be created.
        branch_name (str): The name of the branch to checkout in the new worktree.

    Raises:
        RuntimeError: If the git command fails.
    """
    run_process(["git", "worktree", "add", worktree_path, branch_name], Path.cwd(), check=True)
    print(f"Successfully created worktree at {worktree_path} for branch {branch_name}.")
    
def remove_worktree(worktree_path: str) -> None:
    """
    Remove an existing git worktree at the specified path.

    Args:
        worktree_path (str): The path of the worktree to be removed.

    Raises:
        RuntimeError: If the git command fails.
    """
    run_process(["git", "worktree", "remove", worktree_path], Path.cwd(), check=True)
    print(f"Successfully removed worktree at {worktree_path}.")
    

def list_worktrees() -> list[str]:
    """
    List all existing git worktrees.

    Returns:
        list: A list of paths to the existing worktrees.

    Raises:
        RuntimeError: If the git command fails.
    """
    return [line.split()[0] for line in run_git(Path.cwd(), "worktree", "list").splitlines()]
