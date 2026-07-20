import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flexiv_steward.builds import cmake_gui_command, list_builds, select_build


class BuildInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tempdir.name)
        preset_file = self.project / "flexiv" / "CMakePresets.json"
        preset_file.parent.mkdir(parents=True)
        preset_file.write_text(
            '{"configurePresets": [{"name": "ubuntu-debug", "binaryDir": "${sourceDir}/build/Linux/Debug", '
            '"cacheVariables": {"CMAKE_BUILD_TYPE": "Debug", "THIRDPARTY_PATH": "$env{thirdparty_path}"}}]}'
        )
        cache = self.project / "flexiv" / "build" / "Linux" / "Debug" / "CMakeCache.txt"
        cache.parent.mkdir(parents=True)
        cache.write_text(
            "CMAKE_BUILD_TYPE:STRING=Debug\n"
            "CMAKE_GENERATOR:INTERNAL=Ninja\n"
            "THIRDPARTY_PATH:PATH=/any/host/specific/path\n"
            f"CMAKE_HOME_DIRECTORY:INTERNAL={self.project / 'flexiv'}\n"
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_lists_build_with_matching_preset(self) -> None:
        builds = list_builds(self.project)
        self.assertEqual(builds[0].directory, "flexiv/build/Linux/Debug")
        self.assertEqual(builds[0].presets, ["ubuntu-debug"])
        self.assertEqual(builds[0].options, {})

    def test_selects_build_by_unique_basename(self) -> None:
        build = select_build(list_builds(self.project), "Debug")
        self.assertEqual(build.build_type, "Debug")

    def test_excludes_preset_contradicted_by_cache(self) -> None:
        preset_file = self.project / "flexiv" / "CMakePresets.json"
        preset_file.write_text(
            '{"configurePresets": ['
            '{"name": "base", "binaryDir": "${sourceDir}/build/Linux/Debug"},'
            '{"name": "all", "inherits": "base", "cacheVariables": {"BuildTest_.All": "ON"}}'
            ']}'
        )
        cache = self.project / "flexiv" / "build" / "Linux" / "Debug" / "CMakeCache.txt"
        cache.write_text("BuildTest_.All:BOOL=OFF\n")
        self.assertEqual(list_builds(self.project)[0].presets, ["base"])

    def test_creates_gui_command(self) -> None:
        build = list_builds(self.project)[0]
        with patch("flexiv_steward.builds.shutil.which", return_value="/usr/bin/cmake-gui"):
            command = cmake_gui_command(self.project, build)
        self.assertEqual(command[:3], ["cmake-gui", "-S", str(self.project / "flexiv")])
