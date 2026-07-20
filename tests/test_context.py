import subprocess
import tempfile
import unittest
from pathlib import Path

from flexiv_steward.context import collect_context, discover_project


class ContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tempdir.name)
        subprocess.run(["git", "init"], cwd=self.project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.project, check=True)
        (self.project / "README.md").write_text("test")
        subprocess.run(["git", "add", "README.md"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.project, check=True, capture_output=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_discovers_project_from_nested_directory(self) -> None:
        nested = self.project / "nested"
        nested.mkdir()
        self.assertEqual(discover_project(nested), self.project)

    def test_collects_git_and_cmake_state(self) -> None:
        (self.project / "flexiv").mkdir()
        (self.project / "flexiv" / "CMakePresets.json").write_text(
            '{"configurePresets": [{"name": "ubuntu-debug"}, {"name": "base", "hidden": true}]}'
        )
        context = collect_context(self.project)
        self.assertEqual(context["project"], str(self.project))
        self.assertEqual(context["status"]["changes"], ["?? flexiv/"])
        self.assertEqual(context["cmake"]["presets"], ["ubuntu-debug"])
