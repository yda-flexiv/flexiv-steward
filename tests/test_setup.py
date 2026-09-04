import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flexiv_steward.build import resolve_profile
from flexiv_steward.setup import (
    _submodule_cache_path,
    _submodule_update_commands,
    _update_submodule_cache,
    configure_editor_files,
    setup_worktree,
    submodule_command,
)


class SetupTests(unittest.TestCase):
    def test_submodule_command_is_recursive(self) -> None:
        self.assertEqual(submodule_command(), ["git", "submodule", "update", "--init", "--recursive"])

    def test_submodule_updates_reuse_persistent_common_git_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            project = root / "feature"
            common_git_dir = root / "superproject.git"
            (project / ".gitmodules").parent.mkdir(parents=True)
            (project / ".gitmodules").touch()
            cache = _submodule_cache_path(
                common_git_dir / "flexiv-steward" / "submodules",
                Path("docker"),
            )
            (cache / "objects").mkdir(parents=True)
            (cache / "HEAD").touch()

            with patch(
                "flexiv_steward.setup._git_output",
                side_effect=[
                    f"{common_git_dir}\n",
                    "submodule.docker.path docker\n",
                ],
            ):
                commands = _submodule_update_commands(project)

            self.assertEqual(
                commands,
                [
                    (project, ["git", "submodule", "init"]),
                    (
                        project,
                        [
                            "git", "submodule", "update", "--init",
                            "--reference", str(cache), "docker",
                        ],
                    ),
                ],
            )

    def test_submodule_cache_survives_source_checkout_removal(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            checkout = root / "checkout"
            cache = root / "cache.git"
            subprocess.run(["git", "init", str(checkout)], check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=checkout, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=checkout, check=True)
            subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"], cwd=checkout, check=True,
                           capture_output=True)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=checkout, check=True, capture_output=True, text=True,
            ).stdout.strip()

            self.assertEqual(_update_submodule_cache(checkout, cache), 0)
            self.assertTrue((cache / "objects").is_dir())
            checkout.rename(root / "removed-checkout")
            self.assertEqual(
                subprocess.run(
                    ["git", "--git-dir", str(cache), "rev-parse", f"refs/flexiv-steward/{commit}"],
                    check=True, capture_output=True, text=True,
                ).stdout.strip(),
                commit,
            )

    def test_dry_run_prints_bootstrap_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            project = Path(tempdir)
            self.assertEqual(setup_worktree(project, resolve_profile("rca"), dry_run=True), 0)

    def test_initializes_dispatcher_submodule_before_building_configure_command(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            project = Path(tempdir)

            def initialize_submodules(*args: object, **kwargs: object) -> object:
                dispatcher = project / "docker" / "docker_dispatch.sh"
                dispatcher.parent.mkdir()
                dispatcher.touch()
                return type("Result", (), {"returncode": 0})()

            with (
                patch("flexiv_steward.setup.subprocess.run", side_effect=initialize_submodules),
                patch("flexiv_steward.setup.run_command", return_value=0) as run_command,
                patch("flexiv_steward.setup.configure_editor_files"),
                patch("flexiv_steward.setup.dependency_doctor", return_value=[]),
            ):
                self.assertEqual(setup_worktree(project, resolve_profile("rca"), dry_run=False), 0)

            self.assertIn("docker/docker_dispatch.sh", run_command.call_args.args[0])

    def test_creates_clangd_database_and_projectile_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            project = Path(tempdir)
            source = project / "flexiv" / "build" / "Linux" / "RelWithDebInfo" / "compile_commands.json"
            source.parent.mkdir(parents=True)
            source.touch()
            presets = project / "flexiv" / "CMakePresets.json"
            presets.write_text(json.dumps({"configurePresets": [
                {"name": "base", "binaryDir": "${sourceDir}/build/Linux/RelWithDebInfo"},
                {"name": "rca", "inherits": "base"},
            ]}))
            rewrite = project / "docker" / "rewrite_compile_commands_for_clangd.sh"
            rewrite.parent.mkdir()
            rewrite.write_text("#!/bin/bash\n"
                               "repo=$(dirname \"$(dirname \"$0\")\")\n"
                               "cp \"$FLEXIV_CLANGD_DB_IN\" \"$repo/flexiv/compile_commands.json\"\n")
            rewrite.chmod(0o755)

            configure_editor_files(project, "rca")

            host_database = project / "flexiv" / "compile_commands.json"
            self.assertTrue(host_database.is_file())
            database = project / "compile_commands.json"
            self.assertTrue(database.is_symlink())
            self.assertEqual(database.resolve(), host_database)
            self.assertTrue((project / ".projectile").is_file())
            self.assertEqual((project / ".projectile").read_text(), "")

    def test_creates_clangd_database_in_direct_source_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            project = Path(tempdir)
            source = project / "build" / "Linux" / "RelWithDebInfo" / "compile_commands.json"
            source.parent.mkdir(parents=True)
            source.write_text("[]")
            (project / "CMakePresets.json").write_text(json.dumps({"configurePresets": [
                {"name": "base", "binaryDir": "${sourceDir}/build/Linux/RelWithDebInfo"},
                {"name": "ubuntu-relwithdebinfo-g1", "inherits": "base"},
            ]}))
            rewrite = project / "docker" / "rewrite_compile_commands_for_clangd.sh"
            rewrite.parent.mkdir()
            rewrite.write_text('#!/bin/bash\ncp "$FLEXIV_CLANGD_DB_IN" "$FLEXIV_CLANGD_DB_OUT"\n')
            rewrite.chmod(0o755)

            configure_editor_files(project, "ubuntu-relwithdebinfo-g1")

            self.assertEqual((project / "compile_commands.json").read_text(), "[]")
            self.assertFalse((project / "compile_commands.json").is_symlink())
