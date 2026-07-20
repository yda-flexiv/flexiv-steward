"""
File: src/flexiv_steward/git.py
Description: This module provides functionality for interacting with git repositories.
"""

def git_status() -> str:
    """
    Get the current status of the git repository.

    Returns:
        str: status of the git repository, together with submodule status, and external repository status.

    Raises:
        RuntimeError: If the git command fails.
    """
    import subprocess
    try:
        # Get the status of the git repository
        result = subprocess.run(
            ["git", "status"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        status_output = result.stdout.decode().strip()
        
        # Get the status of submodules
        submodule_result = subprocess.run(
            ["git", "submodule", "status"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        submodule_output = submodule_result.stdout.decode().strip()
        
        # Get the status of external repositories (if any)
        external_result = subprocess.run(
            ["git", "submodule", "foreach", "git", "status"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        external_output = external_result.stdout.decode().strip()
        
        return f"Git Status:\n{status_output}\n\nSubmodule Status:\n{submodule_output}\n\nExternal Repository Status:\n{external_output}"
    
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to get git status: {e.stderr.decode().strip()}") from e