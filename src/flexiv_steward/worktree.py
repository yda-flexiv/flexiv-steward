"""Git worktree operations and non-mutating health checks."""

from __future__ import annotations

import difflib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import TypedDict

from flexiv_steward.errors import ProjectError
from flexiv_steward.layout import bare_workspace
from flexiv_steward.models import WorktreeCreation, WorktreeInfo, WorktreeSummary
from flexiv_steward.process import run_git, run_process


class _RawWorktree(TypedDict, total=False):
    path: str
    head: str
    branch: str | None
    detached: bool
    locked: str | bool
    prunable: str | bool
    bare: bool


def parse_worktree_porcelain(output: str) -> tuple[WorktreeInfo, ...]:
    """Parse Git's porcelain worktree listing."""
    worktrees: list[WorktreeInfo] = []
    current: _RawWorktree = {}
    for line in [*output.splitlines(), ""]:
        if not line:
            if current and "path" in current and not current.get("bare", False):
                worktrees.append(
                    WorktreeInfo(
                        path=Path(current["path"]),
                        head=current.get("head", ""),
                        branch=current.get("branch"),
                        detached=current.get("detached", False),
                        locked=current.get("locked", False),
                        prunable=current.get("prunable", False),
                    )
                )
            current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached":
            current["detached"] = True
        elif key == "locked":
            current["locked"] = value or True
        elif key == "prunable":
            current["prunable"] = value or True
        elif key == "bare":
            current["bare"] = True
    return tuple(worktrees)


def _porcelain_paths(output: str) -> list[str]:
    """Return one current path per NUL-delimited porcelain status entry."""
    paths: list[str] = []
    records = output.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        if len(record) < 4:
            index += 1
            continue
        state = record[:2]
        paths.append(record[3:])
        index += 2 if "R" in state or "C" in state else 1
    return paths


def _last_activity(path: Path, status_output: str) -> tuple[str | None, str | None]:
    """Return the latest Git or changed-file activity as UTC ISO time."""
    candidates: list[tuple[float, str]] = []
    try:
        reflog_time = run_git(path, "reflog", "show", "-1", "--format=%ct", "HEAD")
        if reflog_time:
            candidates.append((float(reflog_time), "reflog"))
    except (ProjectError, ValueError):
        pass
    for relative_path in _porcelain_paths(status_output):
        try:
            candidates.append(((path / relative_path).stat().st_mtime, "working-tree"))
        except OSError:
            pass
    if not candidates:
        try:
            candidates.append((path.stat().st_mtime, "directory"))
        except OSError:
            return None, None
    timestamp, source = max(candidates)
    activity = datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")
    return activity, source


def _inspect_worktree(worktree: WorktreeInfo) -> WorktreeInfo:
    """Add status and activity metadata to one parsed worktree."""
    path = worktree.path
    exists = path.is_dir()
    changes: int | None = None
    activity: str | None = None
    source: str | None = None
    if exists and not worktree.prunable:
        status_output = ""
        try:
            status_output = run_git(path, "status", "--porcelain=v1", "-z")
            changes = len(_porcelain_paths(status_output))
        except ProjectError:
            pass
        activity, source = _last_activity(path, status_output)
    issues: list[str] = []
    if worktree.prunable or not exists:
        issues.append("prunable")
    if worktree.locked:
        issues.append("locked")
    if changes:
        issues.append("dirty")
    return WorktreeInfo(
        path=path,
        head=worktree.head,
        branch=worktree.branch,
        detached=worktree.detached,
        locked=worktree.locked,
        prunable=worktree.prunable,
        exists=exists,
        changes=changes,
        last_activity=activity,
        last_activity_source=source,
        issues=tuple(issues),
    )


def inspect_worktrees(project: str | Path) -> tuple[WorktreeInfo, ...]:
    """Return status and safety signals for every worktree in the repository."""
    project_path = Path(project)
    worktrees = parse_worktree_porcelain(run_git(project_path, "worktree", "list", "--porcelain"))
    worker_count = min(8, len(worktrees))
    if not worker_count:
        return ()
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return tuple(executor.map(_inspect_worktree, worktrees))

def resolve_worktree(project: Path, identifier: str) -> Path:
    """Resolve a worktree by absolute path, directory basename, or branch name."""
    candidates = inspect_worktrees(project)
    requested = Path(identifier).expanduser()
    if requested.is_absolute():
        resolved = requested.resolve()
        matches = [entry for entry in candidates if entry.path.resolve() == resolved]
    else:
        matches = [
            entry
            for entry in candidates
            if entry.path.name == identifier or entry.branch == identifier
        ]
    if not matches:
        choices = sorted(
            {entry.path.name for entry in candidates}
            | {entry.branch for entry in candidates if entry.branch}
        )
        suggestion = difflib.get_close_matches(identifier, choices, n=1, cutoff=0.6)
        hint = f"; did you mean '{suggestion[0]}'?" if suggestion else ""
        raise ProjectError(f"no worktree named '{identifier}'{hint}")
    if len(matches) > 1:
        raise ProjectError(f"worktree name '{identifier}' is ambiguous; provide its absolute path")
    return matches[0].path


def fetch_command(base: str) -> list[str]:
    """Return the fetch arguments that refresh the remote side of *base*."""
    remote, separator, branch = base.partition("/")
    if separator:
        return ["fetch", remote, branch]
    return ["fetch", "origin", base]


def _branch_upstreams(project: Path) -> dict[str, str | None]:
    output = run_git(project, "for-each-ref", "--format=%(refname:short)%09%(upstream:short)", "refs/heads")
    upstreams: dict[str, str | None] = {}
    for line in output.splitlines():
        branch, _, upstream = line.partition("\t")
        upstreams[branch] = upstream or None
    return upstreams


def _branch_creation_parent(project: Path, branch: str) -> str | None:
    """Read the branch's original start point from its reflog, if it has one."""
    try:
        entries = run_git(project, "reflog", "show", "--format=%gs", branch).splitlines()
    except ProjectError:
        return None
    prefix = "branch: Created from "
    for entry in reversed(entries):
        if entry.startswith(prefix):
            parent = entry.removeprefix(prefix).strip()
            return parent if parent and parent != "HEAD" else None
    return None


def collect_worktree_summaries(project: Path) -> tuple[WorktreeSummary, ...]:
    """List every worktree with its checked-out branch and parent branch."""
    worktrees = parse_worktree_porcelain(run_git(project, "worktree", "list", "--porcelain"))
    upstreams = _branch_upstreams(project)
    current = project.resolve()
    summaries: list[WorktreeSummary] = []
    for worktree in worktrees:
        parent = upstreams.get(worktree.branch) if worktree.branch else None
        if not parent and worktree.branch:
            parent = _branch_creation_parent(project, worktree.branch)
        summaries.append(
            WorktreeSummary(
                path=worktree.path,
                branch=worktree.branch,
                parent=parent,
                current=worktree.path.resolve() == current,
                prunable=bool(worktree.prunable),
            )
        )
    return tuple(summaries)


def workspace_root(project: Path) -> Path:
    """Return the bare hub directory or primary worktree that owns *project*."""
    common = Path(run_git(project, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = project / common
    common = common.resolve()
    if run_git(common, "rev-parse", "--is-bare-repository") == "true":
        return bare_workspace(common)
    worktrees = parse_worktree_porcelain(run_git(project, "worktree", "list", "--porcelain"))
    return worktrees[0].path if worktrees else project


def add_worktree(project: Path, branch: str, parent: str) -> WorktreeCreation:
    """Create, attach, or adopt a worktree for *branch* under the workspace root."""
    worktrees = parse_worktree_porcelain(run_git(project, "worktree", "list", "--porcelain"))
    existing = next((item for item in worktrees if item.branch == branch), None)
    if existing is not None:
        if existing.prunable:
            raise ProjectError(f"worktree {existing.path} is prunable; run `fs wt rm {branch}` first")
        if existing.locked:
            raise ProjectError(f"worktree {existing.path} is locked; run `fs wt rm {branch}` first")
        return WorktreeCreation("exists", existing.path)
    destination = workspace_root(project) / branch.replace("/", "-")
    if destination.exists():
        occupant = next(
            (item for item in worktrees if item.path.resolve() == destination.resolve()), None
        )
        if occupant and occupant.branch:
            detail = f"worktree on branch {occupant.branch}"
        elif occupant:
            detail = "another worktree"
        else:
            detail = "a non-Git directory"
        raise ProjectError(f"refusing to create {destination}: already occupied by {detail}")
    if _branch_exists(project, branch):
        note = None
        if _branch_upstreams(project).get(branch) != parent:
            note = f"using existing branch {branch}; from {parent} not used"
        run_process(
            ["git", "worktree", "add", str(destination), branch],
            project, timeout=300, check=True,
        )
        return WorktreeCreation("attached", destination, note)
    probe = run_process(["git", "ls-remote", "--heads", "origin", branch], project, timeout=600)
    if probe.returncode:
        raise ProjectError(f"git ls-remote --heads origin {branch} failed in {project}: {probe.output}")
    if probe.stdout.strip():
        run_process(["git", "fetch", "origin", branch], project, timeout=600, check=True)
        run_process(
            ["git", "worktree", "add", "--track", "-b", branch, str(destination), f"origin/{branch}"],
            project, timeout=300, check=True,
        )
        subject = run_git(project, "log", "-1", "--format=%h %s", f"origin/{branch}")
        note = f"adopted origin/{branch} @ {subject}; from {parent} not used"
        return WorktreeCreation("adopted", destination, note)
    run_process(["git", *fetch_command(parent)], project, timeout=600, check=True)
    run_process(
        ["git", "worktree", "add", "-b", branch, str(destination), parent],
        project, timeout=300, check=True,
    )
    return WorktreeCreation("created", destination)


def _branch_exists(project: Path, branch: str) -> bool:
    result = run_process(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], project)
    if result.returncode not in {0, 1}:
        raise ProjectError(f"git show-ref failed in {project}: {result.output}")
    return result.returncode == 0


def plan_worktree_removal(project: Path, identifier: str) -> Path:
    """Resolve a removable linked worktree, refusing the main or current one."""
    target = resolve_worktree(project, identifier)
    resolved = target.resolve()
    if resolved == project.resolve():
        raise ProjectError("refusing to remove the current worktree")
    if resolved == workspace_root(project).resolve():
        raise ProjectError("refusing to remove the main worktree")
    return target


def remove_worktree(project: Path, target: Path) -> None:
    """Delete a linked worktree directory, submodules included, then prune Git metadata."""
    locked = any(
        worktree.path.resolve() == target.resolve() and worktree.locked
        for worktree in inspect_worktrees(project)
    )
    if locked:
        run_process(["git", "worktree", "unlock", str(target)], project)
    if target.exists():
        shutil.rmtree(target)
    run_git(project, "worktree", "prune")
