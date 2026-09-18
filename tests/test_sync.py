import subprocess
import tempfile
import unittest
from pathlib import Path

from flexiv_steward.formatters import format_sync
from flexiv_steward.models import SyncResult
from flexiv_steward.sync import sync_worktree
from flexiv_steward.worktree import fetch_command


class SyncTests(unittest.TestCase):
    def test_fetches_remote_tracking_branch(self) -> None:
        self.assertEqual(fetch_command("origin/v4.x_develop"), ["fetch", "origin", "v4.x_develop"])

    def test_conflict_output_leaves_resolution_to_user(self) -> None:
        result = SyncResult(
            "conflict",
            Path("/tmp/RemoveSync2"),
            "origin/v4.x_develop",
            ("flexiv/example.cpp",),
            "Automatic merge failed.",
        )
        output = format_sync(result)
        self.assertIn("flexiv/example.cpp", output)
        self.assertIn("git merge --abort", output)
        self.assertNotIn("git checkout --theirs", output)

    def test_stops_on_a_real_merge_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            remote = root / "remote.git"
            project = root / "project"
            worker = root / "feature"
            self._git(root, "init", "--bare", str(remote))
            self._git(root, "init", str(project))
            self._git(project, "config", "user.email", "test@example.com")
            self._git(project, "config", "user.name", "Test")
            (project / "shared.txt").write_text("original\n")
            self._git(project, "add", "shared.txt")
            self._git(project, "commit", "-m", "initial")
            self._git(project, "remote", "add", "origin", str(remote))
            self._git(project, "push", "-u", "origin", "HEAD:master")
            self._git(project, "worktree", "add", "-b", "feature", str(worker))
            (worker / "shared.txt").write_text("feature\n")
            self._git(worker, "commit", "-am", "feature change")
            (project / "shared.txt").write_text("upstream\n")
            self._git(project, "commit", "-am", "upstream change")
            self._git(project, "push", "origin", "HEAD:master")

            result = sync_worktree(project, "feature", "origin/master", dry_run=False)

            self.assertEqual(result.state, "conflict")
            self.assertEqual(result.conflicts, ("shared.txt",))
            self._git(worker, "merge", "--abort")

    @staticmethod
    def _git(directory: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=directory, check=True, capture_output=True)
