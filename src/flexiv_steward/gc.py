"""Non-destructive disk-usage inventory for worktree build directories."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from flexiv_steward.layout import source_root
from flexiv_steward.models import GcCandidate, GcReport, WorktreeInfo
from flexiv_steward.process import run_process
from flexiv_steward.worktree import inspect_worktrees


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
