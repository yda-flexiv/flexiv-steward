"""
File: src/flexiv_steward/git.py
Description: This module provides functionality for interacting with git repositories.
"""

from pathlib import Path

from flexiv_steward.process import run_git

def git_status() -> str:
    """
    Get the current status of the git repository.

    Returns:
        str: status of the git repository, together with submodule status, and external repository status.

    Raises:
        RuntimeError: If the git command fails.
    """
    project = Path.cwd()
    status_output = run_git(project, "status")
    submodule_output = run_git(project, "submodule", "status")
    external_output = run_git(project, "submodule", "foreach", "git", "status")
    return (
        f"Git Status:\n{status_output}\n\nSubmodule Status:\n{submodule_output}"
        f"\n\nExternal Repository Status:\n{external_output}"
    )
