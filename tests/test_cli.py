import io
import json
import shlex
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from flexiv_steward.cli import main


class CliTests(unittest.TestCase):
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

    def _invoke(self, *argv: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exit_error:
                main(list(argv))
        return exit_error.exception.code, stdout.getvalue(), stderr.getvalue()

    def test_help_and_parser_errors_have_cli_exit_codes(self) -> None:
        code, stdout, stderr = self._invoke("--help")
        self.assertEqual(code, 0)
        self.assertIn("{context,wt,builds,deps,build,setup,gc,test,session,branchdiff}", stdout)
        self.assertEqual(stderr, "")

        code, stdout, stderr = self._invoke("--not-an-option")
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("error:", stderr)

    def test_context_json_is_machine_readable(self) -> None:
        code, stdout, stderr = self._invoke("--project", str(self.project), "context", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout)["project"], str(self.project))
        self.assertNotIn("\x1b[", stdout)

    def test_context_keeps_the_compact_layout(self) -> None:
        code, stdout, stderr = self._invoke("--project", str(self.project), "context")
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("Project:", stdout)

    def test_build_dry_run_preserves_nested_routing_and_shell_quoting(self) -> None:
        (self.project / "flexiv").mkdir()
        (self.project / "docker").mkdir()
        (self.project / "docker" / "docker_dispatch.sh").touch()
        code, stdout, stderr = self._invoke(
            "--project", str(self.project), "build", "custom", "--preset", "debug; echo unsafe", "--configure", "--dry-run"
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        commands = [shlex.split(line.removeprefix("$ ")) for line in stdout.splitlines()]
        self.assertEqual(commands[0][-1], "cd flexiv && cmake --preset 'debug; echo unsafe'")
        self.assertEqual(commands[1][-1], "cd flexiv && cmake --build --preset 'debug; echo unsafe'")

    def _add_worktree(self, name: str = "other") -> Path:
        path = self.project / name
        subprocess.run(
            ["git", "worktree", "add", "-b", name.replace(" ", "-"), str(path)],
            cwd=self.project, check=True, capture_output=True,
        )
        return path

    def test_gc_builds_preview_and_delete_preserve_selected_worktree(self) -> None:
        other = self._add_worktree("other with spaces")
        artifact_roots = ("build", "install", "build_yocto", "cmake-build-debug", "flexiv/build", "flexiv/install")
        for root in (self.project, other):
            for name in artifact_roots:
                path = root / name
                path.mkdir(parents=True)
                (path / "artifact").write_text("keep or remove")
        (self.project / "source.txt").write_text("dirty source")
        subprocess.run(["git", "worktree", "lock", str(other)], cwd=self.project, check=True)
        nested = other / "flexiv" / "src"
        nested.mkdir()
        # The selected worktree is nested inside the main checkout. Keep it even
        # when invoked from a subdirectory, and remove the main checkout's builds.
        code, stdout, stderr = self._invoke("--project", str(nested), "gc", "builds", "--dry-run", "--json")
        self.assertEqual((code, stderr), (0, ""))
        result = json.loads(stdout)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["kept_worktree"], str(other))
        self.assertEqual(set(result["paths"]), {str(self.project / name) for name in artifact_roots})
        self.assertEqual(set(result["size_bytes"]), set(result["paths"]))
        self.assertTrue(all(size > 0 for size in result["size_bytes"].values()))
        self.assertEqual(result["total_bytes"], sum(result["size_bytes"].values()))
        self.assertTrue(all((self.project / name).is_dir() for name in artifact_roots))
        code, stdout, stderr = self._invoke("--project", str(nested), "gc", "builds", "--json")
        self.assertEqual((code, stderr), (0, ""))
        self.assertFalse(json.loads(stdout)["dry_run"])
        self.assertEqual(json.loads(stdout)["size_bytes"], result["size_bytes"])
        self.assertEqual(json.loads(stdout)["total_bytes"], result["total_bytes"])
        self.assertTrue(all(not (self.project / name).exists() for name in artifact_roots))
        self.assertTrue(all((other / name / "artifact").is_file() for name in artifact_roots))
        self.assertEqual((self.project / "source.txt").read_text(), "dirty source")
        self.assertTrue((self.project / "README.md").is_file())
        code, stdout, stderr = self._invoke("--project", str(other), "gc", "builds")
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("Removed 0", stdout)
        self.assertIn("0.00 GiB total", stdout)

    def test_gc_builds_prints_sizes_before_and_after_cleanup(self) -> None:
        other = self._add_worktree()
        sizes = {other / "build": 3 * 1024**3, other / "install": 1024**3 // 2}
        for path in sizes:
            path.mkdir()

        def measure(path: Path) -> int:
            self.assertTrue(path.is_dir(), "sizes must be measured before deleting directories")
            return sizes[path]

        for flags, action in ((("--dry-run",), "Would remove"), ((), "Removed")):
            with patch("flexiv_steward.gc._directory_size", side_effect=measure):
                code, stdout, stderr = self._invoke("--project", str(self.project), "gc", "builds", *flags)
            self.assertEqual((code, stderr), (0, ""))
            self.assertIn(f"{action}: {other / 'build'} (3.00 GiB)", stdout)
            self.assertIn(f"{action}: {other / 'install'} (0.50 GiB)", stdout)
            self.assertIn(f"{action} 2 build/install directories (3.50 GiB total).", stdout)
            self.assertTrue(all(path.exists() == bool(flags) for path in sizes))

    def test_gc_builds_skips_symlink_roots_and_does_not_follow_inner_links(self) -> None:
        other = self._add_worktree()
        protected = self.project / "build"
        protected.mkdir()
        (protected / "artifact").write_text("keep")
        (other / "build").symlink_to(protected, target_is_directory=True)
        (other / "flexiv").symlink_to(self.project, target_is_directory=True)
        (other / "install").mkdir()
        (other / "install" / "linked").symlink_to(protected, target_is_directory=True)
        code, stdout, stderr = self._invoke("--project", str(self.project), "gc", "builds", "--json")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(set(json.loads(stdout)["skipped"]), {str(other / "build"), str(other / "flexiv")})
        self.assertEqual((protected / "artifact").read_text(), "keep")
        self.assertFalse((other / "install").exists())

    def test_gc_builds_refuses_tracked_files_before_any_deletion(self) -> None:
        other = self._add_worktree()
        (other / "build").mkdir()
        (other / "install").mkdir()
        (other / "install" / "source.txt").write_text("tracked")
        subprocess.run(["git", "add", "install/source.txt"], cwd=other, check=True)
        code, stdout, stderr = self._invoke("--project", str(self.project), "gc", "builds")
        self.assertEqual(code, 2)
        self.assertIn("refusing to remove tracked files", stderr)
        self.assertTrue((other / "build").is_dir())
        self.assertEqual((other / "install" / "source.txt").read_text(), "tracked")

    def test_gc_builds_refuses_artifact_directory_containing_current_worktree(self) -> None:
        other = self._add_worktree("build/current")
        (self.project / "build" / "artifact").write_text("keep")
        code, stdout, stderr = self._invoke("--project", str(other), "gc", "builds")
        self.assertEqual(code, 2)
        self.assertIn("containing a worktree", stderr)
        self.assertTrue((other / "README.md").is_file())
        self.assertTrue((self.project / "build" / "artifact").is_file())

    def test_gc_builds_removes_locked_dirty_other_worktree_artifacts(self) -> None:
        other = self._add_worktree()
        subprocess.run(["git", "worktree", "lock", str(other)], cwd=self.project, check=True)
        (other / "README.md").write_text("dirty source")
        (other / "build").mkdir()
        (other / "install").mkdir()
        code, stdout, stderr = self._invoke("--project", str(self.project), "gc", "builds")
        self.assertEqual((code, stderr), (0, ""))
        self.assertFalse((other / "build").exists())
        self.assertFalse((other / "install").exists())
        self.assertEqual((other / "README.md").read_text(), "dirty source")

    def test_gc_builds_refuses_tracked_files_in_nested_source_repository(self) -> None:
        other = self._add_worktree()
        source = other / "flexiv"
        (source / "build").mkdir(parents=True)
        (source / "build" / "source.txt").write_text("tracked nested source")
        subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
        subprocess.run(["git", "add", "build/source.txt"], cwd=source, check=True)
        code, stdout, stderr = self._invoke("--project", str(self.project), "gc", "builds")
        self.assertEqual(code, 2)
        self.assertIn("refusing to remove tracked files", stderr)
        self.assertTrue((source / "build" / "source.txt").is_file())
