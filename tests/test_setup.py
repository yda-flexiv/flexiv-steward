import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flexiv_steward.build import resolve_profile
from flexiv_steward.setup import configure_editor_files, setup_worktree, submodule_command


class SetupTests(unittest.TestCase):
    def test_submodule_command_is_recursive(self) -> None:
        self.assertEqual(submodule_command(), ["git", "submodule", "update", "--init", "--recursive"])

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
