import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from flexiv_steward.externals import initialize_externals


class ExternalCacheTests(unittest.TestCase):
    def _git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()

    def _make_remote(self, root: Path) -> tuple[Path, str, str]:
        remote = root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        seed = root / "seed"
        subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=seed, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=seed, check=True)
        (seed / "f.txt").write_text("one")
        subprocess.run(["git", "add", "f.txt"], cwd=seed, check=True)
        subprocess.run(["git", "commit", "-qm", "one"], cwd=seed, check=True)
        older = self._git(seed, "rev-parse", "HEAD")
        (seed / "f.txt").write_text("two")
        subprocess.run(["git", "commit", "-qam", "two"], cwd=seed, check=True)
        subprocess.run(["git", "push", "-q", "origin", "HEAD"], cwd=seed, check=True)
        return remote, older, self._git(seed, "rev-parse", "HEAD")

    def _make_project(self, root: Path, remote: Path, commit: str) -> Path:
        project = root / "worktree"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=project, check=True)
        source = project / "flexiv"
        (source / "external").mkdir(parents=True)
        (source / "CMakeLists.txt").write_text(
            "FlexivAddExternal(flexiv_sw_base\n"
            f"  GIT_URL {remote}\n"
            f"  GIT_TAG {commit}\n"
            "  SOURCE_DIR ${CMAKE_SOURCE_DIR}/external/flexiv_sw_base\n"
            ")\n"
        )
        subprocess.run(["git", "add", "."], cwd=project, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=project, check=True)
        return project

    def test_second_worktree_clones_offline_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            remote, older, _ = self._make_remote(root)
            project = self._make_project(root, remote, older)

            initialize_externals(project, dry_run=False)

            checkout = project / "flexiv" / "external" / "flexiv_sw_base"
            self.assertEqual(self._git(checkout, "rev-parse", "HEAD"), older)
            self.assertEqual(self._git(checkout, "remote", "get-url", "origin"), str(remote))
            self.assertEqual(self._git(checkout, "rev-list", "--count", "HEAD"), "1")
            caches = list((project / ".git" / "flexiv-steward" / "externals-v1").glob("*.git"))
            self.assertEqual(len(caches), 1)
            self.assertEqual(self._git(caches[0], "rev-parse", f"refs/heads/flexiv-steward-{older}"), older)

            second = root / "second"
            subprocess.run(["git", "worktree", "add", "-q", str(second)], cwd=project, check=True)
            remote.rename(root / "remote-offline.git")

            initialize_externals(second, dry_run=False)

            offline = second / "flexiv" / "external" / "flexiv_sw_base"
            self.assertEqual(self._git(offline, "rev-parse", "HEAD"), older)
            self.assertEqual(self._git(offline, "rev-list", "--count", "HEAD"), "1")

    def test_existing_checkout_fetches_the_cached_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            remote, older, tip = self._make_remote(root)
            project = self._make_project(root, remote, older)
            checkout = project / "flexiv" / "external" / "flexiv_sw_base"

            initialize_externals(project, dry_run=False)

            shutil.rmtree(checkout)
            subprocess.run(["git", "clone", "-q", str(remote), str(checkout)], check=True)
            self.assertEqual(self._git(checkout, "rev-parse", "HEAD"), tip)

            remote.rename(root / "remote-offline.git")
            initialize_externals(project, dry_run=False)

            self.assertEqual(self._git(checkout, "cat-file", "-t", older), "commit")
            self.assertEqual(self._git(checkout, "remote", "get-url", "origin"), str(remote))

    def test_existing_checkout_at_cached_commit_does_not_fetch_current_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            remote, older, _ = self._make_remote(root)
            project = self._make_project(root, remote, older)

            initialize_externals(project, dry_run=False)
            checkout = project / "flexiv" / "external" / "flexiv_sw_base"
            branch = self._git(checkout, "branch", "--show-current")
            self.assertEqual(branch, f"flexiv-steward-{older}")

            remote.rename(root / "remote-offline.git")
            initialize_externals(project, dry_run=False)

            self.assertEqual(self._git(checkout, "rev-parse", "HEAD"), older)
            self.assertEqual(self._git(checkout, "branch", "--show-current"), branch)
