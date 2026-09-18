"""Immutable collection models shared by commands and formatters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepositoryStatus:
    branch: str
    upstream: str | None
    ahead: int
    behind: int
    push_target: str | None
    unpushed: int
    changes: tuple[str, ...]


@dataclass(frozen=True)
class SubmoduleContext:
    state: str
    commit: str
    path: str
    description: str
    status: RepositoryStatus | None


@dataclass(frozen=True)
class NestedRepository:
    name: str
    path: Path
    head: str
    branch: str
    status: RepositoryStatus


@dataclass(frozen=True)
class CompileCommandsContext:
    path: Path
    exists: bool


@dataclass(frozen=True)
class CMakeContext:
    preset_file: Path
    presets: tuple[str, ...]
    compile_commands: CompileCommandsContext


@dataclass(frozen=True)
class DockerContext:
    dispatcher: Path
    dispatcher_exists: bool
    available: bool


@dataclass(frozen=True)
class ProjectContext:
    project: Path
    head: str
    status: RepositoryStatus
    submodules: tuple[SubmoduleContext, ...]
    nested_repositories: tuple[NestedRepository, ...]
    cmake: CMakeContext
    docker: DockerContext


@dataclass(frozen=True)
class WorktreeInfo:
    path: Path
    head: str
    branch: str | None = None
    detached: bool = False
    locked: str | bool = False
    prunable: str | bool = False
    exists: bool = False
    changes: int | None = None
    last_activity: str | None = None
    last_activity_source: str | None = None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class BuildDirectory:
    directory: str
    source_directory: str
    build_type: str | None
    generator: str | None
    presets: tuple[str, ...]
    options: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class DependencyStatus:
    kind: str
    name: str
    path: str
    expected: str | None
    actual: str | None
    state: str
    dirty: bool = False


@dataclass(frozen=True)
class SyncResult:
    state: str
    worktree: Path
    base: str
    conflicts: tuple[str, ...]
    output: str


@dataclass(frozen=True)
class SessionSummary:
    source: str
    updated_at: str
    title: str
    prompts: tuple[str, ...]
    commands: tuple[str, ...]


@dataclass(frozen=True)
class Handoff:
    context: ProjectContext
    session_window_days: int
    sessions: tuple[SessionSummary, ...]


@dataclass(frozen=True)
class SubmoduleSpec:
    name: str
    path: Path
    url: str
    commit: str
