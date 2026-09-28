"""Inventory and cleanup of worktree build artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil

from flexiv_steward.context import discover_project
from flexiv_steward.errors import ProjectError
from flexiv_steward.layout import source_root
from flexiv_steward.process import run_git, run_process
from flexiv_steward.worktree import inspect_worktrees, parse_worktree_porcelain


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


@dataclass(frozen=True)
class WorktreeGcItem:
    path: Path
    branch: str | None
    head: str
    last_activity: str | None
    size_bytes: int | None
    eligible: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class WorktreeGcPlan:
    project: Path
    base: str | None
    base_head: str | None
    older_than_days: int
    items: tuple[WorktreeGcItem, ...]
    removed: tuple[Path, ...] = ()


def plan_gc_worktrees(
    project: Path, base: str | None = None, older_than_days: int = 30, *, measure_sizes: bool = True
) -> WorktreeGcPlan:
    """Inventory linked worktrees; only a clean ancestor of *base* is eligible.

    This is deliberately a local Git check. Squash/rebase PR merges are not
    inferred from a branch name or a clean working tree.
    """
    if older_than_days < 0:
        raise ProjectError("--older-than must be non-negative")
    current = discover_project(project)
    bare = run_git(current, "rev-parse", "--is-bare-repository") == "true"
    worktrees = inspect_worktrees(current)
    base_head = run_git(current, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}") if base else None
    primary = None if bare or not worktrees else worktrees[0].path.resolve()
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    items: list[WorktreeGcItem] = []
    for worktree in worktrees:
        path = worktree.path
        resolved = path.resolve()
        reasons: list[str] = []
        if resolved == current or resolved == primary:
            reasons.append("current-or-main")
        if worktree.prunable or not path.is_dir():
            reasons.append("missing-or-prunable")
        if path.is_symlink():
            reasons.append("symlink")
        if worktree.locked:
            reasons.append("locked")
        if worktree.detached or not worktree.branch:
            reasons.append("detached")
        if base and worktree.branch == base.removeprefix("refs/heads/").removeprefix("origin/"):
            reasons.append("base-branch")
        if any(other.path.resolve().is_relative_to(resolved) and other.path.resolve() != resolved
               for other in worktrees):
            reasons.append("contains-worktree")
        if not base_head:
            reasons.append("base-not-specified")
        if worktree.last_activity is None:
            reasons.append("activity-unknown")
        elif datetime.fromisoformat(worktree.last_activity) > cutoff:
            reasons.append("recent")
        size: int | None = None
        if path.is_dir() and not path.is_symlink():
            if measure_sizes:
                try:
                    size = _directory_size(path)
                except (OSError, ProjectError, ValueError):
                    pass
            status = run_process(
                ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all",
                 "--ignored=matching", "--ignore-submodules=none"], path
            )
            if status.returncode:
                reasons.append("status-unavailable")
            elif status.stdout:
                reasons.append("dirty-or-ignored")
            if (path / ".gitmodules").is_file():
                try:
                    modules = run_process(["git", "submodule", "status", "--recursive"], path)
                    if modules.returncode:
                        reasons.append("submodule-check-failed")
                    elif any(line and not line.startswith("-") for line in modules.stdout.splitlines()):
                        reasons.append("initialized-submodules")
                except ProjectError:
                    reasons.append("submodule-check-failed")
        if base_head and worktree.head:
            merged = run_process(["git", "merge-base", "--is-ancestor", worktree.head, base_head], current)
            if merged.returncode == 1:
                reasons.append("not-merged-into-base")
            elif merged.returncode:
                reasons.append("merge-check-failed")
        elif not worktree.head:
            reasons.append("head-unknown")
        items.append(WorktreeGcItem(path, worktree.branch, worktree.head,
                                    worktree.last_activity, size, not reasons, tuple(reasons)))
    return WorktreeGcPlan(current, base, base_head, older_than_days, tuple(items))


def gc_worktrees(project: Path, base: str | None, older_than_days: int, apply: bool) -> WorktreeGcPlan:
    """Preview by default; on apply, recheck each candidate and use Git's safe removal."""
    if apply and not base:
        raise ProjectError("--apply requires --base (the target branch or remote-tracking ref)")
    plan = plan_gc_worktrees(project, base, older_than_days)
    if not apply:
        return plan
    removed: list[Path] = []
    for item in plan.items:
        if not item.eligible:
            continue
        fresh = plan_gc_worktrees(project, base, older_than_days, measure_sizes=False)
        matching = next((entry for entry in fresh.items if entry.path == item.path), None)
        if fresh.base_head != plan.base_head or matching is None or not matching.eligible or matching.head != item.head:
            raise ProjectError(f"worktree changed during cleanup: {item.path}; removed so far: {removed}")
        run_git(plan.project, "worktree", "remove", str(item.path))
        removed.append(item.path)
    return WorktreeGcPlan(plan.project, plan.base, plan.base_head, plan.older_than_days,
                          plan.items, tuple(removed))
