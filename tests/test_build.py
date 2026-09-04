import tempfile
import unittest
import json
from pathlib import Path

from flexiv_steward.build import build_command, configure_command, resolve_profile, workflow_test_command


class BuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tempdir.name)
        (self.project / "flexiv").mkdir()
        (self.project / "docker").mkdir()
        (self.project / "docker" / "docker_dispatch.sh").touch()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_all_profile_uses_ubuntu_execution_container(self) -> None:
        profile = resolve_profile("all")
        self.assertEqual(
            build_command(self.project, profile, ["RobotControlApp"]),
            [
                "bash",
                "docker/docker_dispatch.sh",
                "exec",
                "ubuntu",
                "bash",
                "-c",
                "cd flexiv && cmake --build --preset ubuntu-relwithdebinfo-g1-All --target RobotControlApp",
            ],
        )
        self.assertIn("cmake --preset ubuntu-relwithdebinfo-g1-All", configure_command(self.project, profile)[-1])

    def test_custom_yocto_preset_selects_yocto_harness(self) -> None:
        profile = resolve_profile("custom", "yocto-debug")
        self.assertEqual(profile.platform, "yocto")

    def test_custom_preset_is_quoted_for_the_shell(self) -> None:
        profile = resolve_profile("custom", "debug; echo unsafe")
        shell_command = configure_command(self.project, profile)[-1]
        self.assertIn("cmake --preset 'debug; echo unsafe'", shell_command)

    def test_build_targets_are_quoted_for_the_shell(self) -> None:
        profile = resolve_profile("all")
        shell_command = build_command(self.project, profile, ["unsafe; echo nope"])[-1]
        self.assertIn("--target 'unsafe; echo nope'", shell_command)

    def test_workflow_test_is_project_local(self) -> None:
        script = self.project / "flexiv" / "tool" / "WorkflowTest" / "run_workflow_test.sh"
        script.parent.mkdir(parents=True)
        script.touch()
        self.assertEqual(workflow_test_command(self.project), ["bash", str(script)])

    def test_direct_source_layout_uses_root_and_base_all_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            project = Path(tempdir)
            (project / "docker").mkdir()
            (project / "docker" / "docker_dispatch.sh").touch()
            (project / "CMakePresets.json").write_text(json.dumps({
                "configurePresets": [{"name": "ubuntu-relwithdebinfo-g1"}],
            }))

            command = configure_command(project, resolve_profile("all"))

            self.assertEqual(command[-1], "cmake --preset ubuntu-relwithdebinfo-g1")
