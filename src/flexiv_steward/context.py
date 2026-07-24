"""Project discovery and read-only Flexiv workspace context."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any


class ProjectError(RuntimeError):
    """The requested directory is not a Git worktree."""


def run_git(project: Path, *args: str) -> str:
    """Run Git in *project* and return stripped stdout.

    Git failures are converted to a concise, actionable exception rather than a
    traceback from the command-line program.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ProjectError(f"{shlex.join(['git', *args])} failed: {detail}")
    return result.stdout.strip()


def discover_project(path: str | Path | None = None) -> Path:
    """Return the top-level Git worktree containing *path*."""
    candidate = Path(path or Path.cwd()).expanduser().resolve()
    try:
        return Path(run_git(candidate, "rev-parse", "--show-toplevel")).resolve()
    except FileNotFoundError as error:
        raise ProjectError("Git is not installed or is not on PATH") from error


def _status(project: Path) -> dict[str, Any]:
    porcelain = run_git(project, "status", "--porcelain=v1", "--branch")
    lines = porcelain.splitlines()
    branch = None
    upstream = None
    ahead = 0
    behind = 0
    changes: list[str] = []
    for line in lines:
        if line.startswith("## "):
            branch_line = line[3:]
            branch, _, tracking = branch_line.partition("...")
            if tracking:
                upstream, _, counts = tracking.partition(" ")
                if "ahead " in counts:
                    ahead = int(counts.split("ahead ", 1)[1].split("]", 1)[0].split(",", 1)[0])
                if "behind " in counts:
                    behind = int(counts.split("behind ", 1)[1].split("]", 1)[0].split(",", 1)[0])
        elif line:
            changes.append(line)
    push_target = upstream
    unpushed = ahead
    if branch:
        remote_branches = run_git(project, "for-each-ref", "--format=%(refname:short)", "refs/remotes").splitlines()
        candidates = [ref for ref in remote_branches if ref.endswith(f"/{branch}")]
        if candidates:
            push_target = next((ref for ref in candidates if ref.startswith("origin/")), candidates[0])
            counts = run_git(project, "rev-list", "--left-right", "--count", f"{push_target}...HEAD").split()
            unpushed = int(counts[1])
    return {
        "branch": branch or "HEAD detached",
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "push_target": push_target,
        "unpushed": unpushed,
        "changes": changes,
    }


def _submodules(project: Path) -> list[dict[str, Any]]:
    output = run_git(project, "submodule", "status", "--recursive")
    modules = []
    for line in output.splitlines():
        if not line:
            continue
        state = line[0]
        parts = line[1:].strip().split(maxsplit=2)
        if len(parts) >= 2:
            module = {
                "state": {"-": "uninitialized", "+": "modified", "U": "conflicted"}.get(state, "ok"),
                "commit": parts[0],
                "path": parts[1],
                "description": parts[2] if len(parts) == 3 else "",
            }
            try:
                status = _status(project / parts[1])
            except ProjectError:
                status = None
            module["status"] = status
            modules.append(module)
    return modules


def _presets(project: Path) -> list[str]:
    preset_file = project / "flexiv" / "CMakePresets.json"
    if not preset_file.is_file():
        return []
    try:
        data = json.loads(preset_file.read_text())
    except json.JSONDecodeError:
        return []
    return [preset["name"] for preset in data.get("configurePresets", []) if not preset.get("hidden")]


def _nested_repo(path: Path, name: str) -> dict[str, Any] | None:
    if not path.is_dir():
        return None
    try:
        status = _status(path)
        return {
            "name": name,
            "path": str(path),
            "head": run_git(path, "rev-parse", "--short", "HEAD"),
            "branch": status["branch"],
            "status": status,
        }
    except ProjectError:
        return None


def collect_context(path: str | Path | None = None) -> dict[str, Any]:
    """Collect a compact, read-only snapshot of a Flexiv worktree."""
    project = discover_project(path)
    status = _status(project)
    compile_database = project / "compile_commands.json"
    nested = _nested_repo(project / "flexiv" / "external" / "flexiv_sw_base", "flexiv_sw_base")
    return {
        "project": str(project),
        "head": run_git(project, "rev-parse", "--short", "HEAD"),
        "status": status,
        "submodules": _submodules(project),
        "nested_repositories": [nested] if nested else [],
        "cmake": {
            "preset_file": str(project / "flexiv" / "CMakePresets.json"),
            "presets": _presets(project),
            "compile_commands": {
                "path": str(compile_database),
                "exists": compile_database.exists(),
            },
        },
        "docker": {
            "dispatcher": str(project / "docker" / "docker_dispatch.sh"),
            "dispatcher_exists": (project / "docker" / "docker_dispatch.sh").is_file(),
            "available": shutil.which("docker") is not None,
        },
    }


def format_context(context: dict[str, Any]) -> str:
    """Render context in a concise form suited to humans and agent handoffs."""
    def uncommitted_detail(repository_status: dict[str, Any]) -> str:
        changes = repository_status["changes"]
        return "clean" if not changes else f"{len(changes)} uncommitted entries: {', '.join(changes)}"

    status = context["status"]
    submodule_statuses = [module["status"] for module in context["submodules"] if module["status"]]
    changed_submodules = sum(bool(module_status["changes"]) for module_status in submodule_statuses)
    unpushed_submodules = sum(module_status["unpushed"] for module_status in submodule_statuses)
    docker_module = next((module for module in context["submodules"] if module["path"] == "docker"), None)
    docker_status = docker_module["status"] if docker_module else None
    docker_detail = "not a Git submodule"
    if docker_status:
        docker_detail = f"{uncommitted_detail(docker_status)}; {docker_status['unpushed']} unpushed"
    lines = [
        f"Project: {context['project']}",
        f"Branch: {status['branch']} ({context['head']})",
        f"Working tree: {uncommitted_detail(status)}",
        f"Unpushed commits: {status['unpushed']}",
        f"Submodules: {len(context['submodules'])} ({changed_submodules} with uncommitted changes, {unpushed_submodules} unpushed)",
        f"Docker: {'available' if context['docker']['available'] else 'not found'} ({docker_detail})",
        f"compile_commands.json: {'present' if context['cmake']['compile_commands']['exists'] else 'missing'}",
    ]
    if status["upstream"]:
        lines.append(f"Upstream: {status['upstream']} (+{status['ahead']} / -{status['behind']})")
    if context["nested_repositories"]:
        nested = context["nested_repositories"][0]
        nested_status = nested["status"]
        lines.append(
            f"Nested: {nested['name']} {nested['branch']} "
            f"({nested['head']}, {uncommitted_detail(nested_status)}; {nested_status['unpushed']} unpushed)"
        )
    return "\n".join(lines)
