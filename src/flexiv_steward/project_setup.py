"""
File: src/flexiv_steward/project_setup.py
Description: This module provides functionality for setting up and managing project environments.
"""

def setup_project_for_emacs(project_path: str) -> None:
    """
    Set up the project environment for Emacs.

    Args:
        project_path (str): The path to the project directory.

    Raises:
        RuntimeError: If the setup process fails.
    """
    import os
    import subprocess
    # Create a .projectile at project root
    projectile_file_path = os.path.join(project_path, ".projectile")
    try:
        with open(projectile_file_path, "w") as f:
            f.write(project_path)
        print(f"Created .projectile file at {projectile_file_path}.")
    except Exception as e:
        raise RuntimeError(f"Failed to create .projectile file: {str(e)}") from e
    
    # if git submodules exist, run `git submodule update --init --recursive`
    try:
        subprocess.run(
            ["git", "submodule", "update", "--init", "--recursive"],
            cwd=project_path,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print("Successfully updated git submodules.")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to update git submodules: {e.stderr.decode().strip()}") from e
    # configure project to generate compile_commands.json
    try:
        subprocess.run(
            ["bash", "-c", "docker/shell-docker.sh cd flexiv && cmake --preset=ubuntu-relwithdebinfo-g1-All"],
            cwd=project_path,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print("Successfully configured project to generate compile_commands.json.")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to configure project: {e.stderr.decode().strip()}") from e
    # create soft link to compile_commands.json in project root
    compile_commands_src = os.path.join(project_path, "flexiv", "build", "Linux", "RelWithDebInfo", "compile_commands.json")
    compile_commands_dst = os.path.join(project_path, "compile_commands.json")
    try:
        if os.path.exists(compile_commands_dst):
            os.remove(compile_commands_dst)
        os.symlink(compile_commands_src, compile_commands_dst)
        print(f"Created symlink for compile_commands.json at {compile_commands_dst}.")
    except Exception as e:
        raise RuntimeError(f"Failed to create symlink for compile_commands.json: {str(e)}") from e