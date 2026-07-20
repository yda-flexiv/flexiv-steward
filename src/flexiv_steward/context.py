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
    return {
        "branch": branch or "HEAD detached",
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "changes": changes,
    }


def _submodules(project: Path) -> list[dict[str, str]]:
    output = run_git(project, "submodule", "status", "--recursive")
    modules = []
    for line in output.splitlines():
        if not line:
            continue
        state = line[0]
        parts = line[1:].strip().split(maxsplit=2)
        if len(parts) >= 2:
            modules.append(
                {
                    "state": {"-": "uninitialized", "+": "modified", "U": "conflicted"}.get(state, "ok"),
                    "commit": parts[0],
                    "path": parts[1],
                    "description": parts[2] if len(parts) == 3 else "",
                }
            )
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
            "changes": len(status["changes"]),
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
    status = context["status"]
    lines = [
        f"Project: {context['project']}",
        f"Branch: {status['branch']} ({context['head']})",
        f"Changes: {len(status['changes'])}",
        f"Submodules: {len(context['submodules'])}",
        f"Docker: {'available' if context['docker']['available'] else 'not found'}",
        f"compile_commands.json: {'present' if context['cmake']['compile_commands']['exists'] else 'missing'}",
    ]
    if status["upstream"]:
        lines.append(f"Upstream: {status['upstream']} (+{status['ahead']} / -{status['behind']})")
    if context["nested_repositories"]:
        nested = context["nested_repositories"][0]
        lines.append(f"Nested: {nested['name']} {nested['branch']} ({nested['head']}, {nested['changes']} changes)")
    return "\n".join(lines)
