"""Inventory and cleanup of worktree build artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from flexiv_steward.context import discover_project
from flexiv_steward.errors import ProjectError
from flexiv_steward.layout import source_root
from flexiv_steward.process import run_git, run_process
from flexiv_steward.worktree import parse_worktree_porcelain


def _directory_size(path: Path) -> int:
    result = run_process(["du", "-sk", str(path)], path.parent, timeout=300, check=True)
    blocks = int(result.stdout.split(maxsplit=1)[0])
    return blocks * 1024


@dataclass(frozen=True)
class BuildCleanup:
    kept_worktree: Path
    dry_run: bool
    paths: tuple[Path, ...]
    skipped: tuple[Path, ...]
    size_bytes: dict[Path, int]
    total_bytes: int


def clean_builds(project: Path, dry_run: bool = False) -> BuildCleanup:
    """Remove other worktrees' local build/install roots, preserving source trees.

    Only conventional artifact roots are selected. Symlinked roots are skipped;
    links inside real artifact directories are unlinked without following them.
    Validate the entire plan before deleting any directory.
    """
    current = discover_project(project)
    bare = run_git(current, "rev-parse", "--is-bare-repository") == "true"
    worktrees = parse_worktree_porcelain(run_git(current, "worktree", "list", "--porcelain"))
    roots = {worktree.path.resolve() for worktree in worktrees}
    if not bare and current not in roots:
        raise ProjectError("current worktree is missing from Git's worktree list; nothing was deleted")
    paths: list[Path] = []
    skipped: list[Path] = []
    for worktree in worktrees:
        root = worktree.path.resolve()
        if root == current or not root.is_dir():
            continue
        for source in dict.fromkeys((root, source_root(root))):
            # A wrapper's flexiv/ directory may itself point outside the worktree.
            if source.is_symlink():
                skipped.append(source)
                continue
            for path in sorted(source.iterdir()):
                if path.name not in {"build", "install", "build_yocto"} and not path.name.startswith("cmake-build-"):
                    continue
                if path.is_symlink():
                    skipped.append(path)
                    continue
                if not path.is_dir():
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(root) or any(
                    protected == resolved or protected.is_relative_to(resolved)
                    for protected in roots
                ):
                    raise ProjectError(f"refusing to remove artifact directory containing a worktree: {path}")
                # Prevent a conventional name from causing deletion of tracked source.
                if run_git(source, "ls-files", "--", f":(literal){path.name}"):
                    raise ProjectError(f"refusing to remove tracked files in {path}; nothing was deleted")
                paths.append(path)
    # Measure before deletion so previews and actual cleanup report the same data.
    sizes = {path: _directory_size(path) for path in paths}
    if not dry_run:
        for path in paths:
            shutil.rmtree(path)
    return BuildCleanup(current, dry_run, tuple(paths), tuple(skipped), sizes, sum(sizes.values()))
