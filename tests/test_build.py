import json
import tempfile
import unittest
from pathlib import Path

from flexiv_steward.build import build_command, configure_command, workflow_test_command
from flexiv_steward.errors import ProjectError
from flexiv_steward.presets import build_preset, compile_commands_path, configure_preset, list_presets


class BuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tempdir.name)
        (self.project / "flexiv").mkdir()
        (self.project / "docker").mkdir()
        (self.project / "docker" / "docker_dispatch.sh").touch()
        (self.project / "flexiv" / "CMakePresets.json").write_text(json.dumps({
            "configurePresets": [
                {"name": "base", "hidden": True, "binaryDir": "${sourceDir}/build", "cacheVariables": {
                    "CMAKE_TOOLCHAIN_FILE": "cmake/yocto-toolchain.cmake"
                }},
                {"name": "yocto-custom", "inherits": "base"},
                {"name": "ubuntu-custom", "binaryDir": "${sourceDir}/build/ubuntu"},
            ],
            "buildPresets": [
                {"name": "build-yocto", "configurePreset": "yocto-custom"},
                {"name": "build-ubuntu", "configurePreset": "ubuntu-custom"},
            ],
        }))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_lists_actual_presets_and_uses_build_preset_configure_mapping(self) -> None:
        self.assertEqual(list_presets(self.project), {
            "configure": ["yocto-custom", "ubuntu-custom"],
            "build": ["build-yocto", "build-ubuntu"],
        })
        preset = build_preset(self.project, "build-yocto")
        self.assertEqual(preset.platform, "yocto")
        self.assertEqual(
            build_command(self.project, preset, ["RobotControlApp"]),
            ["bash", "docker/docker_dispatch.sh", "exec", "yocto", "bash", "-c",
             "cd flexiv && cmake --build --preset build-yocto --target RobotControlApp"],
        )
        self.assertEqual(configure_command(self.project, preset)[-1], "cd flexiv && cmake --preset yocto-custom")

    def test_user_preset_and_shell_quoting(self) -> None:
        (self.project / "flexiv" / "CMakeUserPresets.json").write_text(json.dumps({
            "configurePresets": [{"name": "debug; echo unsafe", "inherits": "ubuntu-custom"}],
            "buildPresets": [{"name": "user-build", "configurePreset": "debug; echo unsafe"}],
        }))
        preset = build_preset(self.project, "user-build")
        self.assertEqual(preset.platform, "ubuntu")
        self.assertIn("cmake --preset 'debug; echo unsafe'", configure_command(self.project, preset)[-1])
        self.assertIn("--target 'unsafe; echo nope'", build_command(self.project, preset, ["unsafe; echo nope"])[-1])
        self.assertEqual(compile_commands_path(self.project, "debug; echo unsafe"),
                         self.project / "flexiv" / "build" / "ubuntu" / "compile_commands.json")

    def test_unknown_preset_fails_before_docker(self) -> None:
        with self.assertRaisesRegex(ProjectError, "fs preset list"):
            configure_preset(self.project, "rca")
        with self.assertRaisesRegex(ProjectError, "fs preset list"):
            build_preset(self.project, "rca")

    def test_workflow_test_is_project_local(self) -> None:
        script = self.project / "flexiv" / "tool" / "WorkflowTest" / "run_workflow_test.sh"
        script.parent.mkdir(parents=True)
        script.touch()
        self.assertEqual(workflow_test_command(self.project), ["bash", str(script)])

    def test_direct_source_layout_uses_requested_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            project = Path(tempdir)
            (project / "docker").mkdir()
            (project / "docker" / "docker_dispatch.sh").touch()
            (project / "CMakePresets.json").write_text(json.dumps({
                "configurePresets": [{"name": "ubuntu-static-check", "binaryDir": "${sourceDir}/build/clang-tidy"}],
                "buildPresets": [{"name": "ubuntu-static-check", "configurePreset": "ubuntu-static-check"}],
            }))
            preset = build_preset(project, "ubuntu-static-check")
            self.assertEqual(configure_command(project, preset)[-1], "cmake --preset ubuntu-static-check")
            self.assertEqual(build_command(project, preset, [])[-1], "cmake --build --preset ubuntu-static-check")
