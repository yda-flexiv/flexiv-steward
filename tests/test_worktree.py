import unittest

from flexiv_steward.worktree import parse_worktree_porcelain


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
