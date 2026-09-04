import subprocess
import tempfile
import unittest
from pathlib import Path

from flexiv_steward.context import collect_context, discover_project, format_context


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

    def test_context_reports_nested_and_submodule_commit_state(self) -> None:
        base = self.project / "flexiv" / "external" / "flexiv_sw_base"
        base.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=base, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=base, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=base, check=True)
        (base / "README.md").write_text("base")
        subprocess.run(["git", "add", "README.md"], cwd=base, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=base, check=True, capture_output=True)

        context = collect_context(self.project)
        nested = context["nested_repositories"][0]
        self.assertEqual(nested["status"]["changes"], [])
        self.assertEqual(nested["status"]["ahead"], 0)
        self.assertEqual(nested["status"]["unpushed"], 0)

        rendered = format_context(
            {
                **context,
                "submodules": [
                    {
                        "path": "docker",
                        "status": {"changes": [" M Dockerfile"], "ahead": 2, "unpushed": 2},
                    }
                ],
            }
        )
        self.assertIn("Working tree: 1 uncommitted entries: ?? flexiv/", rendered)
        self.assertIn("Unpushed commits: 0", rendered)
        self.assertIn("Submodules: 1 (1 with uncommitted changes, 2 unpushed)", rendered)
        self.assertIn("Docker: ", rendered)
        self.assertIn("1 uncommitted entries:  M Dockerfile; 2 unpushed", rendered)
        self.assertIn("Nested: flexiv_sw_base", rendered)

    def test_collects_presets_from_direct_source_layout(self) -> None:
        (self.project / "CMakePresets.json").write_text(
            '{"configurePresets": [{"name": "ubuntu-relwithdebinfo-g1"}]}'
        )

        context = collect_context(self.project)

        self.assertEqual(context["cmake"]["presets"], ["ubuntu-relwithdebinfo-g1"])
        self.assertEqual(context["cmake"]["preset_file"], str(self.project / "CMakePresets.json"))
