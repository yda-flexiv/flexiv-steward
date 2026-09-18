import argparse
import io
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from flexiv_steward.cli import _complete_branches, _complete_worktrees, main


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
        self.assertIn("{context,wt,deps,build,setup,gc,test,session,branchdiff}", stdout)
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

    def _add_remote(self, project: Path) -> Path:
        remote = Path(self.tempdir.name) / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=project, check=True)
        subprocess.run(
            ["git", "push", "-q", "origin", "HEAD", "HEAD:release/v4.2"], cwd=project, check=True
        )
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=project, check=True)
        return remote

    def _advance_remote_release(self, remote: Path) -> str:
        pusher = Path(self.tempdir.name) / "pusher"
        subprocess.run(["git", "clone", "-q", str(remote), str(pusher)], check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=pusher, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=pusher, check=True)
        subprocess.run(["git", "checkout", "-q", "release/v4.2"], cwd=pusher, check=True)
        (pusher / "second.txt").write_text("second")
        subprocess.run(["git", "add", "second.txt"], cwd=pusher, check=True)
        subprocess.run(["git", "commit", "-qm", "second"], cwd=pusher, check=True)
        subprocess.run(["git", "push", "-q", "origin", "release/v4.2"], cwd=pusher, check=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=pusher, capture_output=True, text=True, check=True
        ).stdout.strip()

    def test_wt_add_fetches_parent_and_normalizes_folder_name(self) -> None:
        main = Path(self.tempdir.name) / "main"
        main.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=main, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=main, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=main, check=True)
        (main / "file.txt").write_text("x")
        subprocess.run(["git", "add", "file.txt"], cwd=main, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=main, check=True)
        remote = self._add_remote(main)
        expected_head = self._advance_remote_release(remote)

        code, stdout, stderr = self._invoke(
            "--project", str(main),
            "wt", "add", "task/SW-42-fix-thing", "from", "origin/release/v4.2",
        )

        self.assertEqual((code, stderr), (0, ""))
        destination = main / "task-SW-42-fix-thing"
        self.assertTrue(destination.is_dir())
        self.assertIn(str(destination), stdout)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=destination, capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertEqual(head, expected_head)

        code, stdout, stderr = self._invoke("--project", str(main), "wt", "list", "--json")
        self.assertEqual((code, stderr), (0, ""))
        entry = next(item for item in json.loads(stdout) if item["branch"] == "task/SW-42-fix-thing")
        self.assertEqual(entry["parent"], "origin/release/v4.2")

    def test_wt_rm_requires_confirmation_then_removes_and_prunes(self) -> None:
        other = self._add_worktree("task-SW-7")

        with patch("builtins.input", return_value="n"):
            code, stdout, stderr = self._invoke("--project", str(self.project), "wt", "rm", "task-SW-7")
        self.assertEqual(code, 1)
        self.assertIn("Aborted", stdout)
        self.assertTrue(other.is_dir())

        with patch("builtins.input", return_value="y"):
            code, stdout, stderr = self._invoke("--project", str(self.project), "wt", "rm", "task-SW-7")
        self.assertEqual((code, stderr), (0, ""))
        self.assertFalse(other.exists())
        listing = subprocess.run(
            ["git", "worktree", "list"], cwd=self.project, capture_output=True, text=True, check=True
        ).stdout
        self.assertNotIn("task-SW-7", listing)

    def test_wt_rm_refuses_current_worktree(self) -> None:
        code, stdout, stderr = self._invoke("--project", str(self.project), "wt", "rm", self.project.name)
        self.assertEqual(code, 2)
        self.assertIn("refusing to remove the current worktree", stderr)

    def test_completers_suggest_worktrees_and_branches(self) -> None:
        self._add_worktree("task-SW-7")
        parsed = argparse.Namespace(project=str(self.project))

        self.assertIn("task-SW-7", _complete_worktrees(prefix="task", parsed_args=parsed))
        self.assertNotIn("task-SW-7", _complete_worktrees(prefix="nothing", parsed_args=parsed))
        self.assertIn("task-SW-7", _complete_branches(prefix="task", parsed_args=parsed))

        missing = argparse.Namespace(project=str(Path(self.tempdir.name) / "not-a-repo"))
        self.assertEqual(_complete_worktrees(parsed_args=missing), [])
        self.assertEqual(_complete_branches(parsed_args=missing), [])

    def test_wt_switch_opens_a_shell_in_the_target_worktree(self) -> None:
        other = self._add_worktree("shell-target")
        script = Path(self.tempdir.name) / "shell.sh"
        output = Path(self.tempdir.name) / "shell-cwd.txt"
        script.write_text(f'#!/bin/sh\npwd > "{output}"\n')
        script.chmod(0o755)

        with patch.dict(os.environ, {"SHELL": str(script)}):
            code, stdout, stderr = self._invoke(
                "--project", str(self.project), "wt", "switch", "shell-target"
            )

        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(output.read_text().strip(), str(other))
        self.assertIn(str(other), stdout)
