"""Git worktree operations and non-mutating health checks."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flexiv_steward.context import ProjectError, run_git


def parse_worktree_porcelain(output: str) -> list[dict[str, Any]]:
    """Parse ``git worktree list --porcelain`` without losing path metadata."""
    worktrees: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*output.splitlines(), ""]:
        if not line:
            if current:
                current.setdefault("locked", False)
                current.setdefault("prunable", False)
                current.setdefault("branch", None)
                current.setdefault("detached", False)
                worktrees.append(current)
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
    return worktrees


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


def _inspect_worktree(worktree: dict[str, Any]) -> dict[str, Any]:
    """Add status and activity metadata to one parsed worktree."""
    worktree = dict(worktree)
    path = Path(worktree["path"])
    worktree["exists"] = path.is_dir()
    worktree["changes"] = None
    worktree["last_activity"] = None
    worktree["last_activity_source"] = None
    if path.is_dir() and not worktree["prunable"]:
        status_output = ""
        try:
            status_output = run_git(path, "status", "--porcelain=v1", "-z")
            worktree["changes"] = len(_porcelain_paths(status_output))
        except ProjectError:
            worktree["changes"] = None
        activity, source = _last_activity(path, status_output)
        worktree["last_activity"] = activity
        worktree["last_activity_source"] = source
    issues = []
    if worktree["prunable"] or not worktree["exists"]:
        issues.append("prunable")
    if worktree["locked"]:
        issues.append("locked")
    if worktree["changes"]:
        issues.append("dirty")
    worktree["issues"] = issues
    return worktree


def inspect_worktrees(project: str | Path) -> list[dict[str, Any]]:
    """Return status and safety signals for every worktree in the repository."""
    project_path = Path(project)
    worktrees = parse_worktree_porcelain(run_git(project_path, "worktree", "list", "--porcelain"))
    worker_count = min(8, len(worktrees))
    if not worker_count:
        return []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(executor.map(_inspect_worktree, worktrees))


def format_worktree_doctor(worktrees: list[dict[str, Any]]) -> str:
    """Render worktree health in a compact, actionable table."""
    lines = ["STATE     CHANGES  LAST ACTIVE       BRANCH                         PATH"]
    for worktree in worktrees:
        state = ",".join(worktree["issues"]) or "healthy"
        changes = "?" if worktree["changes"] is None else str(worktree["changes"])
        branch = worktree["branch"] or ("detached" if worktree["detached"] else "-")
        activity = worktree.get("last_activity")
        active = datetime.fromisoformat(activity).astimezone().strftime("%Y-%m-%d %H:%M") if activity else "-"
        lines.append(f"{state:<9} {changes:<8} {active:<17} {branch:<30} {worktree['path']}")
    problem_count = sum(bool(worktree["issues"]) for worktree in worktrees)
    lines.append(f"\n{len(worktrees)} worktrees; {problem_count} need attention.")
    if any(worktree["prunable"] for worktree in worktrees):
        lines.append("Run `git worktree prune` only after reviewing the prunable entries.")
    return "\n".join(lines)

def create_worktree(worktree_path: str, branch_name: str) -> None:
    """
    Create a new git worktree at the specified path and checkout the given branch.

    Args:
        worktree_path (str): The path where the new worktree will be created.
        branch_name (str): The name of the branch to checkout in the new worktree.

    Raises:
        RuntimeError: If the git command fails.
    """
    try:
        # Create the new worktree and checkout the specified branch
        subprocess.run(
            ["git", "worktree", "add", worktree_path, branch_name],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print(f"Successfully created worktree at {worktree_path} for branch {branch_name}.")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to create worktree: {e.stderr.decode().strip()}") from e
    
def remove_worktree(worktree_path: str) -> None:
    """
    Remove an existing git worktree at the specified path.

    Args:
        worktree_path (str): The path of the worktree to be removed.

    Raises:
        RuntimeError: If the git command fails.
    """
    try:
        # Remove the specified worktree
        subprocess.run(
            ["git", "worktree", "remove", worktree_path],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print(f"Successfully removed worktree at {worktree_path}.")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to remove worktree: {e.stderr.decode().strip()}") from e
    

def list_worktrees() -> list:
    """
    List all existing git worktrees.

    Returns:
        list: A list of paths to the existing worktrees.

    Raises:
        RuntimeError: If the git command fails.
    """
    try:
        # List all existing worktrees
        result = subprocess.run(
            ["git", "worktree", "list"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        worktrees = result.stdout.decode().strip().splitlines()
        return [wt.split()[0] for wt in worktrees]  # Return only the paths
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to list worktrees: {e.stderr.decode().strip()}") from e
