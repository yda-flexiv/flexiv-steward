"""Inventory and cleanup of worktree build artifacts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil

from flexiv_steward.context import discover_project
from flexiv_steward.errors import ProjectError
from flexiv_steward.layout import source_root
from flexiv_steward.models import GcCandidate, GcReport, WorktreeInfo
from flexiv_steward.process import run_git, run_process
from flexiv_steward.worktree import inspect_worktrees, parse_worktree_porcelain


def _directory_size(path: Path) -> int:
    result = run_process(["du", "-sk", str(path)], path.parent, timeout=300, check=True)
    blocks = int(result.stdout.split(maxsplit=1)[0])
    return blocks * 1024


def _candidate(worktree: WorktreeInfo, build: Path, older_than_days: int) -> GcCandidate:
    now = datetime.now(timezone.utc)
    timestamps = [datetime.fromtimestamp(build.stat().st_mtime, timezone.utc)]
    if worktree.last_activity:
        timestamps.append(datetime.fromisoformat(worktree.last_activity))
    last_activity = max(timestamps)
    age_days = max(0, (now - last_activity).days)
    clean = worktree.changes == 0
    if worktree.prunable or not worktree.exists:
        reclaimable, reason = True, "prunable worktree"
    elif worktree.locked:
        reclaimable, reason = False, "locked worktree"
    elif age_days < older_than_days:
        reclaimable, reason = False, "recently active; source dirty" if not clean else "recently active"
    elif not clean:
        reclaimable, reason = True, "inactive build; source dirty"
    else:
        reclaimable, reason = True, "clean inactive worktree"
    return GcCandidate(
        path=build,
        worktree=worktree.path,
        size_bytes=_directory_size(build),
        last_activity=last_activity.isoformat(timespec="seconds"),
        age_days=age_days,
        reclaimable=reclaimable,
        reason=reason,
    )


def collect_gc_report(project: Path, older_than_days: int = 30) -> GcReport:
    """Measure build roots in every linked worktree without deleting anything."""
    worktrees = inspect_worktrees(project)
    pairs = [
        (worktree, source_root(worktree.path) / "build")
        for worktree in worktrees
        if worktree.exists and (source_root(worktree.path) / "build").is_dir()
    ]
    if not pairs:
        return GcReport((), 0, 0, older_than_days)
    with ThreadPoolExecutor(max_workers=min(8, len(pairs))) as executor:
        candidates = tuple(
            executor.map(lambda pair: _candidate(pair[0], pair[1], older_than_days), pairs)
        )
    ordered = tuple(sorted(candidates, key=lambda item: item.size_bytes, reverse=True))
    return GcReport(
        candidates=ordered,
        total_bytes=sum(item.size_bytes for item in ordered),
        reclaimable_bytes=sum(item.size_bytes for item in ordered if item.reclaimable),
        older_than_days=older_than_days,
    )


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
    worktrees = parse_worktree_porcelain(run_git(current, "worktree", "list", "--porcelain"))
    roots = {worktree.path.resolve() for worktree in worktrees}
    if current not in roots:
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
