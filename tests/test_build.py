import tempfile
import unittest
from pathlib import Path

from flexiv_steward.build import build_command, configure_command, resolve_profile, workflow_test_command


class BuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tempdir.name)
        (self.project / "docker").mkdir()
        (self.project / "docker" / "docker_dispatch.sh").touch()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_all_profile_uses_ubuntu_dispatcher(self) -> None:
        profile = resolve_profile("all")
        self.assertEqual(
            build_command(self.project, profile, ["RobotControlApp"]),
            ["bash", "docker/docker_dispatch.sh", "build", "ubuntu", "ubuntu-relwithdebinfo-g1-All", "RobotControlApp"],
        )
        self.assertIn("cmake --preset ubuntu-relwithdebinfo-g1-All", configure_command(self.project, profile)[-1])

    def test_custom_yocto_preset_selects_yocto_harness(self) -> None:
        profile = resolve_profile("custom", "yocto-debug")
        self.assertEqual(profile.platform, "yocto")

    def test_workflow_test_is_project_local(self) -> None:
        script = self.project / "flexiv" / "tool" / "WorkflowTest" / "run_workflow_test.sh"
        script.parent.mkdir(parents=True)
        script.touch()
        self.assertEqual(workflow_test_command(self.project), ["bash", str(script)])
