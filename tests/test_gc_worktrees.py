import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from flexiv_steward.cli import main


class WorktreeGcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.project = Path(self.tempdir.name) / "project"
        self.project.mkdir()
        self._git(self.project, "init", "-q")
        self._git(self.project, "config", "user.email", "test@example.com")
        self._git(self.project, "config", "user.name", "Test")
        (self.project / "README.md").write_text("initial\n")
        self._git(self.project, "add", "README.md")
        self._git(self.project, "commit", "-qm", "initial")
        self.base = self._git(self.project, "branch", "--show-current")

    def _git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
        ).stdout.strip()

    def _add(self, branch: str) -> Path:
        path = Path(self.tempdir.name) / branch
        self._git(self.project, "worktree", "add", "-q", "-b", branch, str(path))
        return path

    def _invoke(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as result:
                main(["--project", str(self.project), "gc", "worktrees", *args])
        return result.exception.code, stdout.getvalue(), stderr.getvalue()

    def test_preview_then_apply_only_merged_clean_worktree(self) -> None:
        merged = self._add("merged")
        (merged / "feature.txt").write_text("feature\n")
        self._git(merged, "add", "feature.txt")
        self._git(merged, "commit", "-qm", "feature")
        self._git(self.project, "merge", "--ff-only", "merged")
        unmerged = self._add("unmerged")
        (unmerged / "other.txt").write_text("other\n")
        self._git(unmerged, "add", "other.txt")
        self._git(unmerged, "commit", "-qm", "other")

        code, stdout, stderr = self._invoke("--base", self.base, "--older-than", "0", "--json")
        self.assertEqual((code, stderr), (0, ""))
        preview = json.loads(stdout)
        entries = {item["branch"]: item for item in preview["items"]}
        self.assertTrue(entries["merged"]["eligible"])
        self.assertGreater(entries["merged"]["size_bytes"], 0)
        self.assertIn("not-merged-into-base", entries["unmerged"]["reasons"])
        self.assertIn("current-or-main", entries[self.base]["reasons"])
        self.assertTrue(merged.exists())

        code, stdout, stderr = self._invoke("--base", self.base, "--older-than", "0", "--apply", "--json")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["removed"], [str(merged)])
        self.assertFalse(merged.exists())
        self.assertTrue(unmerged.exists())
        self.assertEqual(self._git(self.project, "rev-parse", "merged"),
                         self._git(self.project, "rev-parse", self.base))

    def test_dirty_ignored_locked_and_squash_equivalent_are_kept(self) -> None:
        (self.project / ".gitignore").write_text("build/\n")
        self._git(self.project, "add", ".gitignore")
        self._git(self.project, "commit", "-qm", "ignore builds")
        ignored = self._add("ignored")
        (ignored / "build").mkdir()
        (ignored / "build" / "artifact").write_text("keep")
        locked = self._add("locked")
        self._git(self.project, "worktree", "lock", str(locked))
        squash = self._add("squash")
        (squash / "same.txt").write_text("same\n")
        self._git(squash, "add", "same.txt")
        self._git(squash, "commit", "-qm", "feature")
        (self.project / "same.txt").write_text("same\n")
        self._git(self.project, "add", "same.txt")
        self._git(self.project, "commit", "-qm", "squash equivalent")

        code, stdout, stderr = self._invoke("--base", self.base, "--older-than", "0", "--apply", "--json")
        self.assertEqual((code, stderr), (0, ""))
        entries = {item["branch"]: item for item in json.loads(stdout)["items"]}
        self.assertIn("dirty-or-ignored", entries["ignored"]["reasons"])
        self.assertIn("locked", entries["locked"]["reasons"])
        self.assertIn("not-merged-into-base", entries["squash"]["reasons"])
        self.assertTrue(all(path.exists() for path in (ignored, locked, squash)))

    def test_apply_requires_base_and_age_must_be_valid(self) -> None:
        worktree = self._add("candidate")
        code, stdout, stderr = self._invoke("--json")
        self.assertEqual((code, stderr), (0, ""))
        entry = next(item for item in json.loads(stdout)["items"] if item["branch"] == "candidate")
        self.assertIn("base-not-specified", entry["reasons"])
        self.assertTrue(worktree.exists())

        code, stdout, stderr = self._invoke("--base", self.base, "--json")
        self.assertEqual((code, stderr), (0, ""))
        entry = next(item for item in json.loads(stdout)["items"] if item["branch"] == "candidate")
        self.assertIn("recent", entry["reasons"])

        (worktree / "untracked.txt").write_text("keep")
        code, stdout, stderr = self._invoke("--base", self.base, "--older-than", "0", "--json")
        self.assertEqual((code, stderr), (0, ""))
        entry = next(item for item in json.loads(stdout)["items"] if item["branch"] == "candidate")
        self.assertIn("dirty-or-ignored", entry["reasons"])

        code, _, stderr = self._invoke("--apply")
        self.assertEqual(code, 2)
        self.assertIn("--apply requires --base", stderr)
        code, _, stderr = self._invoke("--older-than", "-1")
        self.assertEqual(code, 2)
        self.assertIn("--older-than must be non-negative", stderr)

    def test_initialized_submodule_is_not_reported_as_removable(self) -> None:
        module = Path(self.tempdir.name) / "module"
        module.mkdir()
        self._git(module, "init", "-q")
        self._git(module, "config", "user.email", "test@example.com")
        self._git(module, "config", "user.name", "Test")
        (module / "module.txt").write_text("module\n")
        self._git(module, "add", "module.txt")
        self._git(module, "commit", "-qm", "initial")
        self._git(self.project, "-c", "protocol.file.allow=always", "submodule", "add", str(module), "mod")
        self._git(self.project, "commit", "-qam", "add module")
        worktree = self._add("with-module")
        self._git(worktree, "-c", "protocol.file.allow=always", "submodule", "update", "--init")

        code, stdout, stderr = self._invoke("--base", self.base, "--older-than", "0", "--apply", "--json")
        self.assertEqual((code, stderr), (0, ""))
        entry = next(item for item in json.loads(stdout)["items"] if item["branch"] == "with-module")
        self.assertIn("initialized-submodules", entry["reasons"])
        self.assertTrue(worktree.exists())
