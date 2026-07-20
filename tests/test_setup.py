import tempfile
import unittest
from pathlib import Path

from flexiv_steward.build import resolve_profile
from flexiv_steward.setup import setup_worktree, submodule_command


class SetupTests(unittest.TestCase):
    def test_submodule_command_is_recursive(self) -> None:
        self.assertEqual(submodule_command(), ["git", "submodule", "update", "--init", "--recursive"])

    def test_dry_run_prints_bootstrap_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            project = Path(tempdir)
            self.assertEqual(setup_worktree(project, resolve_profile("rca"), dry_run=True), 0)
