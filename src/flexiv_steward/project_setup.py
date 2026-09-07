"""
File: src/flexiv_steward/project_setup.py
Description: This module provides functionality for setting up and managing project environments.
"""

from pathlib import Path

from flexiv_steward.errors import ProjectError
from flexiv_steward.process import run_process


def setup_project_for_emacs(project_path: str) -> None:
    """
    Set up the project environment for Emacs.

    Args:
        project_path (str): The path to the project directory.

    Raises:
        RuntimeError: If the setup process fails.
    """
    project = Path(project_path).expanduser().resolve()
    # Create a .projectile at project root
    projectile_file_path = project / ".projectile"
    try:
        projectile_file_path.write_text(str(project))
        print(f"Created .projectile file at {projectile_file_path}.")
    except OSError as error:
        raise ProjectError(f"Failed to create .projectile file: {error}") from error
    
    # if git submodules exist, run `git submodule update --init --recursive`
    run_process(
        ["git", "submodule", "update", "--init", "--recursive"],
        project,
        timeout=600,
        check=True,
    )
    print("Successfully updated git submodules.")
    # configure project to generate compile_commands.json
    run_process(
        ["bash", "-c", "docker/shell-docker.sh cd flexiv && cmake --preset=ubuntu-relwithdebinfo-g1-All"],
        project,
        timeout=None,
        capture_output=False,
        check=True,
    )
    print("Successfully configured project to generate compile_commands.json.")
    # create soft link to compile_commands.json in project root
    compile_commands_src = project / "flexiv" / "build" / "Linux" / "RelWithDebInfo" / "compile_commands.json"
    compile_commands_dst = project / "compile_commands.json"
    try:
        if compile_commands_dst.exists() or compile_commands_dst.is_symlink():
            compile_commands_dst.unlink()
        compile_commands_dst.symlink_to(compile_commands_src)
        print(f"Created symlink for compile_commands.json at {compile_commands_dst}.")
    except OSError as error:
        raise ProjectError(f"Failed to create symlink for compile_commands.json: {error}") from error
