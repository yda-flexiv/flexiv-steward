"""Human-readable formatting kept separate from read-only collection logic."""

from __future__ import annotations

import shlex
from datetime import datetime

from flexiv_steward.models import (
    BuildDirectory,
    DependencyStatus,
    GcReport,
    Handoff,
    ProjectContext,
    RepositoryStatus,
    SyncResult,
    WorktreeInfo,
)


def _uncommitted_detail(status: RepositoryStatus) -> str:
    return "clean" if not status.changes else f"{len(status.changes)} uncommitted entries: {', '.join(status.changes)}"


def format_context(context: ProjectContext) -> str:
    submodule_statuses = tuple(module.status for module in context.submodules if module.status)
    changed_submodules = sum(bool(status.changes) for status in submodule_statuses)
    unpushed_submodules = sum(status.unpushed for status in submodule_statuses)
    docker_module = next((module for module in context.submodules if module.path == "docker"), None)
    docker_status = docker_module.status if docker_module else None
    docker_detail = "not a Git submodule"
    if docker_status:
        docker_detail = f"{_uncommitted_detail(docker_status)}; {docker_status.unpushed} unpushed"
    lines = [
        f"Project: {context.project}",
        f"Branch: {context.status.branch} ({context.head})",
        f"Working tree: {_uncommitted_detail(context.status)}",
        f"Unpushed commits: {context.status.unpushed}",
        f"Submodules: {len(context.submodules)} ({changed_submodules} with uncommitted changes, {unpushed_submodules} unpushed)",
        f"Docker: {'available' if context.docker.available else 'not found'} ({docker_detail})",
        f"compile_commands.json: {'present' if context.cmake.compile_commands.exists else 'missing'}",
    ]
    if context.status.upstream:
        lines.append(
            f"Upstream: {context.status.upstream} (+{context.status.ahead} / -{context.status.behind})"
        )
    if context.nested_repositories:
        nested = context.nested_repositories[0]
        lines.append(
            f"Nested: {nested.name} {nested.branch} "
            f"({nested.head}, {_uncommitted_detail(nested.status)}; {nested.status.unpushed} unpushed)"
        )
    return "\n".join(lines)


def format_worktree_doctor(worktrees: tuple[WorktreeInfo, ...]) -> str:
    lines = ["STATE     CHANGES  LAST ACTIVE       BRANCH                         PATH"]
    for worktree in worktrees:
        state = ",".join(worktree.issues) or "healthy"
        changes = "?" if worktree.changes is None else str(worktree.changes)
        branch = worktree.branch or ("detached" if worktree.detached else "-")
        active = (
            datetime.fromisoformat(worktree.last_activity).astimezone().strftime("%Y-%m-%d %H:%M")
            if worktree.last_activity
            else "-"
        )
        lines.append(f"{state:<9} {changes:<8} {active:<17} {branch:<30} {worktree.path}")
    problem_count = sum(bool(worktree.issues) for worktree in worktrees)
    lines.append(f"\n{len(worktrees)} worktrees; {problem_count} need attention.")
    if any(worktree.prunable for worktree in worktrees):
        lines.append("Run `git worktree prune` only after reviewing the prunable entries.")
    return "\n".join(lines)


def format_builds(builds: list[BuildDirectory]) -> str:
    if not builds:
        return "No configured CMake builds found under the source build directory."
    lines = ["BUILD DIRECTORY                              TYPE             PRESET / CACHE CONFIGURATION"]
    for build in builds:
        preset = ", ".join(build.presets) or "custom or modified"
        options = ", ".join(f"{key}={value}" for key, value in build.options)
        detail = preset if not options else f"{preset}; {options}"
        lines.append(f"{build.directory:<44} {(build.build_type or '-'): <16} {detail}")
    return "\n".join(lines)


def format_dependency_doctor(statuses: list[DependencyStatus], verbose: bool = False) -> str:
    if not statuses:
        return "No declared external repositories or submodules found."
    bootstrap = sum(status.state in {"missing", "uninitialized"} for status in statuses)
    errors = sum(status.state not in {"ok", "missing", "uninitialized"} for status in statuses)
    dirty = sum(status.dirty for status in statuses)
    externals = sum(status.kind == "external" for status in statuses)
    submodules = sum(status.kind == "submodule" for status in statuses)
    health = "OK" if not errors and not dirty and not bootstrap else "BOOTSTRAP REQUIRED" if bootstrap else "ATTENTION"
    lines = [
        f"Dependency health: {health}",
        f"Checked {len(statuses)} dependencies ({externals} externals, {submodules} submodules).",
    ]
    displayed = statuses if verbose else [status for status in statuses if status.state != "ok" or status.dirty]
    if displayed:
        lines.extend(["", "STATE        TYPE       DIRTY  DEPENDENCY                         EXPECTED    ACTUAL"])
    for status in displayed:
        expected = status.expected[:12] if status.expected else "-"
        actual = status.actual[:12] if status.actual else "-"
        lines.append(
            f"{status.state:<12} {status.kind:<10} {'yes' if status.dirty else 'no':<6} "
            f"{status.name:<34} {expected:<11} {actual}"
        )
    lines.append(f"\n{errors} version errors; {bootstrap} bootstrap required; {dirty} dirty checkouts.")
    if errors:
        lines.append(
            "Do not build until mismatches are corrected. Run `fs setup <profile>` "
            "(for example, `fs setup rca`) to reconfigure and restore CMake-managed externals, "
            "then re-run the build."
        )
    elif bootstrap:
        lines.append("Run `fs setup <profile>` to initialize submodules and CMake-managed external repositories.")
    elif dirty:
        lines.append("Dirty dependencies are allowed, but reduce build reproducibility.")
    elif not verbose:
        lines.append("Use `fs deps doctor --verbose` to show every checked revision.")
    return "\n".join(lines)


def _sync_instructions(result: SyncResult) -> str:
    return "\n".join(
        [
            "Resolve the conflicted files, then complete the merge:",
            f"  cd {shlex.quote(str(result.worktree))}",
            "  git add <resolved-files>",
            "  git commit",
            "Or abandon this synchronization attempt:",
            "  git merge --abort",
        ]
    )


def format_sync(result: SyncResult) -> str:
    lines = [f"Worktree: {result.worktree}", f"Base: {result.base}", f"Result: {result.state}"]
    if result.output:
        lines.extend(["", result.output])
    if result.conflicts:
        lines.extend(
            ["", "Conflicted files:", *(f"  {path}" for path in result.conflicts), "", _sync_instructions(result)]
        )
    elif result.state in {"dirty", "merge-in-progress"}:
        lines.extend(["", "Commit, stash, or discard local work before retrying."])
    return "\n".join(lines)


def format_handoff(data: Handoff) -> str:
    context = data.context
    lines = [
        f"Project handoff: {context.project}",
        f"Branch: {context.status.branch} ({context.head})",
        f"Current changes: {len(context.status.changes)}",
        f"Recent sessions ({data.session_window_days} days): {len(data.sessions)}",
    ]
    for session in data.sessions:
        lines.append(f"\n- [{session.source}] {session.updated_at}: {session.title[:180]}")
        if session.commands:
            lines.append("  Commands: " + "; ".join(session.commands))
        for prompt in session.prompts[-1:]:
            if prompt != session.title:
                lines.append("  Latest: " + prompt[:240])
    return "\n".join(lines)


def _gib(size: int) -> str:
    return f"{size / (1024 ** 3):.2f} GiB"


def format_gc(report: GcReport) -> str:
    lines = [
        "DRY RUN: no files will be deleted.",
        "RECLAIM  SIZE        AGE     REASON                              BUILD DIRECTORY",
    ]
    for candidate in report.candidates:
        age = f"{candidate.age_days}d" if candidate.age_days is not None else "-"
        lines.append(
            f"{'yes' if candidate.reclaimable else 'no':<8} {_gib(candidate.size_bytes):<11} "
            f"{age:<7} {candidate.reason:<35} {candidate.path}"
        )
    lines.extend(
        [
            "",
            f"Found {_gib(report.total_bytes)} across {len(report.candidates)} build directories.",
            f"Conservative reclaimable estimate: {_gib(report.reclaimable_bytes)} "
            f"(unlocked and inactive for at least {report.older_than_days} days, or prunable).",
        ]
    )
    return "\n".join(lines)
