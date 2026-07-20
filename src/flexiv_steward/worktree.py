"""
File: src/flexiv_steward/worktree.py
Description: This module provides functionality for CRUD operations on git worktrees.
"""

def create_worktree(worktree_path: str, branch_name: str) -> None:
    """
    Create a new git worktree at the specified path and checkout the given branch.

    Args:
        worktree_path (str): The path where the new worktree will be created.
        branch_name (str): The name of the branch to checkout in the new worktree.

    Raises:
        RuntimeError: If the git command fails.
    """
    import subprocess

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
    import subprocess

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
    import subprocess

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