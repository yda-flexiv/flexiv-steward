"""Git worktree operations and non-mutating health checks."""

from __future__ import annotations

import subprocess
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


def inspect_worktrees(project: str | Path) -> list[dict[str, Any]]:
    """Return status and safety signals for every worktree in the repository."""
    project_path = Path(project)
    worktrees = parse_worktree_porcelain(run_git(project_path, "worktree", "list", "--porcelain"))
    for worktree in worktrees:
        path = Path(worktree["path"])
        worktree["exists"] = path.is_dir()
        worktree["changes"] = None
        if path.is_dir() and not worktree["prunable"]:
            try:
                worktree["changes"] = len(run_git(path, "status", "--porcelain").splitlines())
            except ProjectError:
                worktree["changes"] = None
        issues = []
        if worktree["prunable"] or not worktree["exists"]:
            issues.append("prunable")
        if worktree["locked"]:
            issues.append("locked")
        if worktree["changes"]:
            issues.append("dirty")
        worktree["issues"] = issues
    return worktrees


def format_worktree_doctor(worktrees: list[dict[str, Any]]) -> str:
    """Render worktree health in a compact, actionable table."""
    lines = ["STATE     CHANGES  BRANCH                         PATH"]
    for worktree in worktrees:
        state = ",".join(worktree["issues"]) or "healthy"
        changes = "?" if worktree["changes"] is None else str(worktree["changes"])
        branch = worktree["branch"] or ("detached" if worktree["detached"] else "-")
        lines.append(f"{state:<9} {changes:<8} {branch:<30} {worktree['path']}")
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
