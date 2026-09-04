import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flexiv_steward.worktree import (
    _last_activity,
    _porcelain_paths,
    format_worktree_doctor,
    parse_worktree_porcelain,
)


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
"""
        worktrees = parse_worktree_porcelain(output)
        self.assertEqual(worktrees[0]["path"], "/tmp/main tree")
        self.assertEqual(worktrees[0]["branch"], "main")
        self.assertFalse(worktrees[0]["prunable"])
        self.assertTrue(worktrees[1]["detached"])
        self.assertEqual(worktrees[1]["locked"], "maintenance")
        self.assertEqual(worktrees[1]["prunable"], "stale gitdir")

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

    def test_formats_last_activity_column(self) -> None:
        output = format_worktree_doctor([{
            "path": "/tmp/main",
            "branch": "main",
            "detached": False,
            "changes": 0,
            "issues": [],
            "prunable": False,
            "last_activity": "2026-09-04T03:02:00+00:00",
        }])

        self.assertIn("LAST ACTIVE", output)
        expected = datetime.fromisoformat("2026-09-04T03:02:00+00:00").astimezone().strftime("%Y-%m-%d %H:%M")
        self.assertIn(expected, output)
