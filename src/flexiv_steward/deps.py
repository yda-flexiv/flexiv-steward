"""Detect version drift in Flexiv external repositories and Git submodules."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from flexiv_steward.context import run_git


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    path: str
    expected: str | None
    actual: str | None
    state: str
    dirty: bool = False


_EXTERNAL = re.compile(r"FlexivAddExternal\(\s*(?P<name>[A-Za-z0-9_]+)(?P<body>.*?)\n\)", re.DOTALL)
_TAG = re.compile(r"\bGIT_TAG\s+(?P<tag>[0-9a-fA-F]{7,40})")
_SOURCE = re.compile(r"\bSOURCE_DIR\s+\$\{CMAKE_SOURCE_DIR\}/(?P<path>[^\s)]+)")


def declared_externals(project: Path) -> list[tuple[str, Path, str]]:
    """Read direct external dependencies declared by the top-level CMake project."""
    cmake = project / "flexiv" / "CMakeLists.txt"
    if not cmake.is_file():
        return []
    declared = []
    for match in _EXTERNAL.finditer(cmake.read_text(errors="replace")):
        tag = _TAG.search(match["body"])
        source = _SOURCE.search(match["body"])
        if tag and source:
            declared.append((match["name"], project / "flexiv" / source["path"], tag["tag"]))
    return declared


def _repository_status(name: str, path: Path, expected: str | None) -> DependencyStatus:
    if not path.is_dir() or not (path / ".git").exists():
        return DependencyStatus(name, str(path), expected, None, "missing")
    actual = run_git(path, "rev-parse", "HEAD")
    dirty = bool(run_git(path, "status", "--porcelain"))
    state = "ok" if not expected or actual.startswith(expected) else "mismatch"
    return DependencyStatus(name, str(path), expected, actual, state, dirty)


def _submodule_statuses(project: Path) -> list[DependencyStatus]:
    statuses = []
    for line in run_git(project, "submodule", "status", "--recursive").splitlines():
        if not line:
            continue
        prefix = line[0]
        parts = line[1:].strip().split(maxsplit=2)
        if len(parts) < 2:
            continue
        commit, relative_path = parts[:2]
        path = project / relative_path
        if prefix == "-":
            state, actual, dirty = "uninitialized", None, False
        elif prefix == "U":
            state, actual, dirty = "conflicted", commit, True
        elif prefix == "+":
            state, actual = "mismatch", commit
            dirty = bool(run_git(path, "status", "--porcelain")) if path.is_dir() else False
        else:
            state, actual = "ok", commit
            dirty = bool(run_git(path, "status", "--porcelain")) if path.is_dir() else False
        statuses.append(DependencyStatus(relative_path, str(path), commit, actual, state, dirty))
    return statuses


def dependency_doctor(project: Path) -> list[DependencyStatus]:
    """Return dependency-version and cleanliness status without changing anything."""
    externals = [_repository_status(name, path, tag) for name, path, tag in declared_externals(project)]
    return [*externals, *_submodule_statuses(project)]


def has_dependency_errors(statuses: list[DependencyStatus]) -> bool:
    return any(status.state != "ok" for status in statuses)


def format_dependency_doctor(statuses: list[DependencyStatus]) -> str:
    """Render version drift in a compact form suitable for build preflight output."""
    if not statuses:
        return "No declared external repositories or submodules found."
    lines = ["STATE        DIRTY  DEPENDENCY                         EXPECTED    ACTUAL"]
    for status in statuses:
        expected = status.expected[:12] if status.expected else "-"
        actual = status.actual[:12] if status.actual else "-"
        lines.append(f"{status.state:<12} {'yes' if status.dirty else 'no':<6} {status.name:<34} {expected:<11} {actual}")
    errors = sum(status.state != "ok" for status in statuses)
    dirty = sum(status.dirty for status in statuses)
    lines.append(f"\n{len(statuses)} dependencies; {errors} version errors; {dirty} dirty checkouts.")
    if errors:
        lines.append("Do not build until mismatches are corrected; re-run CMake after restoring the declared revision.")
    return "\n".join(lines)


def dependency_json(statuses: list[DependencyStatus]) -> list[dict[str, Any]]:
    return [asdict(status) for status in statuses]
