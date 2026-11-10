"""Remember which CMake preset a worktree uses and which presets people prefer.

Two small JSON files back this module:

* ``<workspace>/.steward/preset_history.json`` counts configure presets used by
  every worktree of one workspace, so ``fs setup`` can default to the most used
  one. The workspace root is the bare hub (``~/ws/flexiv_sw``) or the primary
  worktree of a clone.
* ``<worktree>/.steward/preset.json`` remembers the preset last configured or
  built in one worktree, so ``fs build --target App`` needs no preset argument.

Both files are local state: they are never committed and are kept out of
``git status`` through the repository's ``info/exclude``.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from flexiv_steward.errors import ProjectError
from flexiv_steward.process import run_git
from flexiv_steward.worktree import workspace_root

STEWARD_DIRECTORY = ".steward"
HISTORY_FILE = "preset_history.json"
CURRENT_FILE = "preset.json"
SCHEMA_VERSION = 1
IGNORE_ENTRY = ".steward/"


@dataclass(frozen=True)
class PresetUsage:
    """How often one configure preset was used, and when it was last used."""

    name: str
    uses: int
    last_used: str


@dataclass(frozen=True)
class WorktreePreset:
    """The preset a single worktree remembers for later commands."""

    configure_preset: str
    preset: str | None
    recorded_at: str


def history_path(workspace: Path) -> Path:
    """Return the workspace-wide preset history path."""
    return workspace / STEWARD_DIRECTORY / HISTORY_FILE


def current_path(project: Path) -> Path:
    """Return the per-worktree remembered preset path."""
    return project / STEWARD_DIRECTORY / CURRENT_FILE


def workspace_of(project: Path) -> Path:
    """Return the workspace root that owns *project*."""
    return workspace_root(project)


def load_history(workspace: Path) -> tuple[PresetUsage, ...]:
    """Return recorded preset usage, most used first.

    A missing or unreadable file yields an empty history instead of failing the
    command: history is a convenience, never a prerequisite.
    """
    data = _read_mapping(history_path(workspace))
    records = data.get("presets")
    if not isinstance(records, dict):
        return tuple()
    usages: list[PresetUsage] = []
    for raw_name, raw_record in cast(dict[object, object], records).items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        uses = 0
        last_used = ""
        if isinstance(raw_record, dict):
            raw_uses = cast(dict[object, object], raw_record).get("uses")
            if isinstance(raw_uses, int) and not isinstance(raw_uses, bool) and raw_uses > 0:
                uses = raw_uses
            raw_last_used = cast(dict[object, object], raw_record).get("last_used")
            if isinstance(raw_last_used, str):
                last_used = raw_last_used
        elif isinstance(raw_record, int) and not isinstance(raw_record, bool):
            # Tolerate a hand-written {"preset": count} file.
            uses = max(raw_record, 0)
        if uses <= 0:
            continue
        usages.append(PresetUsage(raw_name, uses, last_used))
    return tuple(sorted(usages, key=lambda usage: (-usage.uses, -_timestamp(usage.last_used), usage.name)))


def preferred_preset(workspace: Path) -> str | None:
    """Return the most frequently used configure preset, if any."""
    usages = load_history(workspace)
    return usages[0].name if usages else None


def record_usage(workspace: Path, configure_preset: str, *, now: datetime | None = None) -> None:
    """Count one use of *configure_preset* in the workspace history."""
    name = configure_preset.strip()
    if not name:
        raise ProjectError("cannot record an empty preset name")
    records: dict[str, dict[str, object]] = {
        usage.name: {"uses": usage.uses, "last_used": usage.last_used}
        for usage in load_history(workspace)
    }
    previous_uses = records.get(name, {}).get("uses", 0)
    uses = (previous_uses if isinstance(previous_uses, int) and not isinstance(previous_uses, bool) else 0) + 1
    records[name] = {"uses": uses, "last_used": _timestamp_text(now)}
    _write_mapping(history_path(workspace), {"version": SCHEMA_VERSION, "presets": records}, project=workspace)


def load_current(project: Path) -> WorktreePreset | None:
    """Return the preset remembered for *project*, or ``None`` when unset."""
    data = _read_mapping(current_path(project))
    configure_preset = data.get("configure_preset")
    if not isinstance(configure_preset, str) or not configure_preset.strip():
        return None
    raw_preset = data.get("preset")
    preset = raw_preset.strip() if isinstance(raw_preset, str) and raw_preset.strip() else None
    raw_recorded_at = data.get("recorded_at")
    recorded_at = raw_recorded_at if isinstance(raw_recorded_at, str) else ""
    return WorktreePreset(configure_preset.strip(), preset, recorded_at)


def store_current(
    project: Path,
    *,
    configure_preset: str,
    preset: str | None,
    now: datetime | None = None,
) -> None:
    """Remember the preset names that later ``fs build`` runs should reuse."""
    configure_name = configure_preset.strip()
    if not configure_name:
        raise ProjectError("cannot remember an empty preset name")
    build_name = preset.strip() if preset and preset.strip() else None
    _write_mapping(
        current_path(project),
        {
            "version": SCHEMA_VERSION,
            "configure_preset": configure_name,
            "preset": build_name,
            "recorded_at": _timestamp_text(now),
        },
        project=project,
    )


def clear_current(project: Path) -> bool:
    """Forget the preset remembered for *project*; return whether one was removed.

    The ``.steward`` directory is removed as well when that empties it, so a
    cleared worktree keeps no leftover state directory.
    """
    path = current_path(project)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    try:
        path.parent.rmdir()
    except OSError:
        # Another state file still lives there, or the directory is read-only.
        pass
    return True


def ensure_ignored(project: Path) -> None:
    """Keep the steward state directory out of ``git status`` in *project*.

    The entry goes to the repository's local ``info/exclude`` rather than to a
    tracked ``.gitignore``, so no worktree ever looks dirty because of this
    state. Failure is reported with ``ProjectError`` and callers treat it as
    best effort.
    """
    exclude = Path(run_git(project, "rev-parse", "--git-path", "info/exclude"))
    if not exclude.is_absolute():
        exclude = project / exclude
    try:
        content = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    except OSError:
        content = ""
    patterns = {line.strip().rstrip("/") for line in content.splitlines() if line.strip()}
    if IGNORE_ENTRY.rstrip("/") in patterns:
        return
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as handle:
        if content and not content.endswith("\n"):
            handle.write("\n")
        handle.write(f"{IGNORE_ENTRY}\n")


def _timestamp_text(now: datetime | None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        # A naive timestamp is taken as UTC so stored times never depend on the
        # machine's local time zone.
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, OverflowError, OSError):
        return 0.0


def _read_mapping(path: Path) -> dict[str, object]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in cast(dict[object, object], raw).items()}


def _write_mapping(path: Path, payload: dict[str, object], *, project: Path) -> None:
    try:
        ensure_ignored(project)
    except (ProjectError, OSError):
        # Outside Git, or with a read-only exclude file, the state is still
        # usable; it may simply show up as an untracked directory.
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
