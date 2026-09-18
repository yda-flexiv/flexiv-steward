import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flexiv_steward.context import discover_project
from flexiv_steward.errors import ProjectError
from flexiv_steward.formatters import format_worktree_doctor, format_worktree_list
from flexiv_steward.models import WorktreeInfo, WorktreeSummary
from flexiv_steward.worktree import (
    _last_activity,
    _porcelain_paths,
    add_worktree,
    collect_worktree_summaries,
    parse_worktree_porcelain,
    plan_worktree_removal,
    remove_worktree,
    resolve_worktree,
)


class WorktreeCommandTests(unittest.TestCase):
    def _make_repo(self, root: Path) -> Path:
        remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        project = root / "main"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=project, check=True)
        (project / "README.md").write_text("test")
        subprocess.run(["git", "add", "README.md"], cwd=project, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=project, check=True)
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=project, check=True)
        subprocess.run(["git", "push", "-q", "origin", "HEAD", "HEAD:release/v4.2"], cwd=project, check=True)
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=project, check=True)
        return project

    def test_collect_worktree_summaries_reports_branch_and_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            project = self._make_repo(root)
            branch = subprocess.run(
                ["git", "branch", "--show-current"], cwd=project, capture_output=True, text=True, check=True
            ).stdout.strip()
            subprocess.run(
                ["git", "worktree", "add", "-q", "-b", "task/SW-1-fix", str(root / "task-SW-1-fix"), "origin/release/v4.2"],
                cwd=project, check=True,
            )
            subprocess.run(
                ["git", "worktree", "add", "-q", "-b", "bug/SW-2", str(root / "bug-SW-2"), branch],
                cwd=project, check=True,
            )

            summaries = collect_worktree_summaries(project)
            by_branch = {summary.branch: summary for summary in summaries}

            self.assertEqual(by_branch["task/SW-1-fix"].parent, "origin/release/v4.2")
            self.assertEqual(by_branch["bug/SW-2"].parent, branch)
            self.assertTrue(by_branch[branch].current)
            self.assertFalse(by_branch["task/SW-1-fix"].current)

    def _make_bare_hub(self, root: Path) -> Path:
        remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        seed = root / "seed"
        seed.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=seed, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=seed, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=seed, check=True)
        (seed / "README.md").write_text("test")
        subprocess.run(["git", "add", "README.md"], cwd=seed, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=seed, check=True)
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
        subprocess.run(["git", "push", "-q", "origin", "HEAD", "HEAD:release/v4.2"], cwd=seed, check=True)
        hub = root / "flexiv_sw"
        subprocess.run(["git", "clone", "-q", "--bare", str(remote), str(hub)], check=True)
        subprocess.run(
            ["git", "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"],
            cwd=hub, check=True,
        )
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=hub, check=True)
        return hub

    def test_bare_hub_supports_discovery_list_add_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            hub = self._make_bare_hub(root)
            subprocess.run(
                ["git", "worktree", "add", "-q", "-b", "task/SW-1", str(hub / "SW-1")],
                cwd=hub, check=True,
            )

            self.assertEqual(discover_project(hub), hub)
            summaries = collect_worktree_summaries(hub)
            self.assertEqual([summary.branch for summary in summaries], ["task/SW-1"])

            destination = add_worktree(hub / "SW-1", "task/SW-2", "origin/release/v4.2")
            self.assertEqual(destination, hub / "task-SW-2")
            self.assertTrue((destination / "README.md").is_file())

            target = plan_worktree_removal(hub, "SW-1")
            self.assertEqual(target, hub / "SW-1")
            remove_worktree(hub, target)
            self.assertFalse(target.exists())

    def test_collect_worktree_summaries_marks_prunable_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            project = self._make_repo(root)
            target = root / "gone"
            subprocess.run(
                ["git", "worktree", "add", "-q", "-b", "gone", str(target)], cwd=project, check=True
            )
            shutil.rmtree(target)

            summaries = collect_worktree_summaries(project)
            gone = next(summary for summary in summaries if summary.branch == "gone")

            self.assertTrue(gone.prunable)
            self.assertIn("(prunable)", format_worktree_list(summaries))

    def test_resolve_worktree_accepts_branch_name_and_suggests_typos(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            project = self._make_repo(root)
            target = root / "SW-7794-sync-v3"
            subprocess.run(
                ["git", "worktree", "add", "-q", "-b", "task/SW-7794-sync-v3", str(target)],
                cwd=project, check=True,
            )

            self.assertEqual(resolve_worktree(project, "task/SW-7794-sync-v3"), target)
            self.assertEqual(resolve_worktree(project, "SW-7794-sync-v3"), target)
            with self.assertRaises(ProjectError) as error:
                resolve_worktree(project, "SW-7994-sync-v3")
            self.assertIn("did you mean 'SW-7794-sync-v3'", str(error.exception))

    def test_plan_and_remove_worktree_prunes_locked_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            project = self._make_repo(root)
            target = root / "task-SW-3"
            subprocess.run(
                ["git", "worktree", "add", "-q", "-b", "task/SW-3", str(target)], cwd=project, check=True
            )
            subprocess.run(["git", "worktree", "lock", str(target)], cwd=project, check=True)

            self.assertEqual(plan_worktree_removal(project, "task-SW-3"), target)
            remove_worktree(project, target)

            self.assertFalse(target.exists())
            listing = subprocess.run(
                ["git", "worktree", "list"], cwd=project, capture_output=True, text=True, check=True
            ).stdout
            self.assertNotIn("task-SW-3", listing)

    def test_plan_worktree_removal_refuses_current_and_main(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            project = self._make_repo(root)
            linked = root / "other"
            subprocess.run(
                ["git", "worktree", "add", "-q", "-b", "other", str(linked)], cwd=project, check=True
            )

            with self.assertRaises(ProjectError):
                plan_worktree_removal(project, project.name)
            with self.assertRaises(ProjectError):
                plan_worktree_removal(linked, linked.name)
            with self.assertRaises(ProjectError):
                plan_worktree_removal(linked, project.name)


class WorktreeTests(unittest.TestCase):
    def test_preserves_paths_and_health_metadata(self) -> None:
        output = """worktree /tmp/main tree
HEAD abcdef0123456789
branch refs/heads/main

worktree /tmp/old-tree
HEAD 0123456789abcdef
detached
locked maintenance
prunable stale gitdir

worktree /tmp/bare.git
bare
"""
        worktrees = parse_worktree_porcelain(output)
        self.assertEqual(worktrees[0].path, Path("/tmp/main tree"))
        self.assertEqual(worktrees[0].branch, "main")
        self.assertFalse(worktrees[0].prunable)
        self.assertTrue(worktrees[1].detached)
        self.assertEqual(worktrees[1].locked, "maintenance")
        self.assertEqual(worktrees[1].prunable, "stale gitdir")
        self.assertEqual(len(worktrees), 2)

    def test_porcelain_paths_handle_renames_and_embedded_newlines(self) -> None:
        output = " M normal.cpp\0R  renamed.cpp\0old.cpp\0?? line\nbreak.cpp\0"
        self.assertEqual(_porcelain_paths(output), ["normal.cpp", "renamed.cpp", "line\nbreak.cpp"])

    def test_latest_activity_includes_uncommitted_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            project = Path(tempdir)
            changed = project / "changed.cpp"
            changed.touch()
            os.utime(changed, (2_000, 2_000))
            with patch("flexiv_steward.worktree.run_git", return_value="1000"):
                activity, source = _last_activity(project, " M changed.cpp\0")

            self.assertEqual(activity, "1970-01-01T00:33:20+00:00")
            self.assertEqual(source, "working-tree")

    def test_format_worktree_list_aligns_long_branches(self) -> None:
        summaries = (
            WorktreeSummary(Path("/tmp/short"), "task/SW-1-short", "origin/develop", True),
            WorktreeSummary(
                Path("/tmp/long"),
                "task/SW-8286-run-automated-tests-with-sanitizer",
                "origin/release/v4.2",
                False,
            ),
        )

        header, first, second = format_worktree_list(summaries).splitlines()

        self.assertIn("BRANCH", header)
        self.assertEqual(first.index("origin/develop"), second.index("origin/release/v4.2"))
        self.assertEqual(first.index("/tmp/short"), second.index("/tmp/long"))

    def test_formats_last_activity_column(self) -> None:
        output = format_worktree_doctor((
            WorktreeInfo(
                path=Path("/tmp/main"),
                head="abc",
                branch="main",
                exists=True,
                changes=0,
                last_activity="2026-09-04T03:02:00+00:00",
            ),
        ))

        self.assertIn("LAST ACTIVE", output)
        expected = datetime.fromisoformat("2026-09-04T03:02:00+00:00").astimezone().strftime("%Y-%m-%d %H:%M")
        self.assertIn(expected, output)
