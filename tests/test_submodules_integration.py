import subprocess
import tempfile
import unittest
from pathlib import Path

from flexiv_steward.errors import ProjectError
from flexiv_steward.gitcache import cache_path, ensure_cache
from flexiv_steward.submodules import initialize_submodules


class SubmoduleIntegrationTests(unittest.TestCase):
    def test_exact_commit_cache_bootstraps_two_real_worktrees_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            module = root / "module-source"
            remote = root / "module.git"
            superproject = root / "super"
            first_worktree = root / "feature-one"
            second_worktree = root / "feature-two"

            self._init(module)
            (module / "payload.txt").write_text("required commit\n")
            self._git(module, "add", "payload.txt")
            self._git(module, "commit", "-m", "required")
            required = self._output(module, "rev-parse", "HEAD")
            (module / "payload.txt").write_text("newer tip\n")
            self._git(module, "commit", "-am", "newer")
            newer = self._output(module, "rev-parse", "HEAD")
            self._git(root, "clone", "--bare", str(module), str(remote))

            self._init(superproject)
            self._git(
                superproject,
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(remote),
                "deps/module",
            )
            self._git(superproject / "deps" / "module", "checkout", "--detach", required)
            self._git(
                superproject,
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(remote),
                "deps/module-two",
            )
            self._git(superproject, "add", ".gitmodules", "deps/module", "deps/module-two")
            self._git(superproject, "commit", "-m", "pin submodule")
            self._git(superproject, "worktree", "add", "-b", "feature-one", str(first_worktree))

            self.assertEqual(initialize_submodules(first_worktree, dry_run=False), 0)
            self.assertEqual(self._output(first_worktree / "deps" / "module", "rev-parse", "HEAD"), required)
            self.assertEqual(
                self._output(first_worktree / "deps" / "module-two", "rev-parse", "HEAD"),
                newer,
            )
            cache_root = superproject / ".git" / "flexiv-steward" / "submodules-v2"
            caches = tuple(cache_root.glob("*.git"))
            self.assertEqual(len(caches), 1)
            self.assertTrue((caches[0] / "shallow").is_file())

            self._git(superproject, "worktree", "add", "-b", "feature-two", str(second_worktree))
            remote.rename(root / "module-offline.git")
            self.assertEqual(initialize_submodules(second_worktree, dry_run=False), 0)
            self.assertEqual(self._output(second_worktree / "deps" / "module", "rev-parse", "HEAD"), required)
            self.assertEqual(
                self._output(second_worktree / "deps" / "module-two", "rev-parse", "HEAD"),
                newer,
            )

    def test_existing_submodule_fetches_new_pin_from_cache_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            module = root / "module-source"
            remote = root / "module.git"
            superproject = root / "super"
            self._init(module)
            (module / "payload.txt").write_text("old pin\n")
            self._git(module, "add", "payload.txt")
            self._git(module, "commit", "-m", "old")
            old = self._output(module, "rev-parse", "HEAD")
            self._git(root, "clone", "--bare", str(module), str(remote))
            self._init(superproject)
            self._git(superproject, "-c", "protocol.file.allow=always",
                      "submodule", "add", str(remote), "module")
            self._git(superproject, "commit", "-am", "pin old")
            self.assertEqual(initialize_submodules(superproject, dry_run=False), 0)
            checkout = superproject / "module"

            (module / "payload.txt").write_text("new pin\n")
            self._git(module, "commit", "-am", "new")
            required = self._output(module, "rev-parse", "HEAD")
            self._git(module, "push", str(remote), "HEAD:refs/heads/new-pin")
            self._git(superproject, "update-index", "--cacheinfo", "160000", required, "module")
            self._git(superproject, "commit", "-m", "pin new")
            caches = superproject / ".git" / "flexiv-steward" / "submodules-v2"
            ensure_cache(cache_path(caches, str(remote)), str(remote), (required,))
            self.assertNotEqual(
                subprocess.run(["git", "cat-file", "-e", required], cwd=checkout,
                               capture_output=True).returncode, 0,
            )
            remote.rename(root / "module-offline.git")

            self.assertEqual(initialize_submodules(superproject, dry_run=False), 0)
            self.assertEqual(self._output(checkout, "rev-parse", "HEAD"), required)
            self.assertEqual((checkout / "payload.txt").read_text(), "new pin\n")
            self.assertEqual(self._output(checkout, "remote", "get-url", "origin"), str(remote))
            self.assertEqual(initialize_submodules(superproject, dry_run=False), 0)

            # A pin change must still refuse to overwrite local edits and report Git's reason.
            (checkout / "payload.txt").write_text("local edits\n")
            self._git(superproject, "update-index", "--cacheinfo", "160000", old, "module")
            self._git(superproject, "commit", "-m", "pin old again")
            with self.assertRaisesRegex(ProjectError, "local changes"):
                initialize_submodules(superproject, dry_run=False)
            self.assertEqual((checkout / "payload.txt").read_text(), "local edits\n")
            self.assertEqual(self._output(checkout, "rev-parse", "HEAD"), required)

    def _init(self, directory: Path) -> None:
        directory.mkdir()
        self._git(directory, "init")
        self._git(directory, "config", "user.email", "test@example.com")
        self._git(directory, "config", "user.name", "Test")

    @staticmethod
    def _git(directory: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=directory, check=True, capture_output=True)

    @staticmethod
    def _output(directory: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=directory, check=True, capture_output=True, text=True
        ).stdout.strip()
