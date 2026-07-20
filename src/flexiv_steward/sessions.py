"""Local, read-only summaries of agent sessions scoped to one worktree."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from flexiv_steward.context import collect_context


SOURCES = ("codex", "opencode", "cursor", "copilot")
_SECRET = re.compile(r"(?i)\b(token|api[_-]?key|password|secret)\b\s*[:=]\s*\S+")


@dataclass
class SessionSummary:
    source: str
    updated_at: str
    title: str
    prompts: list[str]
    commands: list[str]


def _redact(text: str) -> str:
    return _SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", " ".join(text.split()))


def _brief(text: str, limit: int = 240) -> str:
    """Keep a handoff useful without turning it back into a transcript dump."""
    text = _redact(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().isoformat(timespec="minutes")


def _is_project_path(candidate: str | Path, project: Path) -> bool:
    try:
        return Path(candidate).expanduser().resolve() == project.resolve()
    except OSError:
        return False


def _json_lines(path: Path) -> Iterable[dict[str, Any]]:
    try:
        for line in path.read_text(errors="replace").splitlines():
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
    except OSError:
        return


def _codex_sessions(project: Path, since: datetime, home: Path) -> list[SessionSummary]:
    records = []
    for path in (home / ".codex" / "sessions").glob("**/*.jsonl"):
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < since:
            continue
        events = list(_json_lines(path))
        meta = next((event.get("payload", {}) for event in events if event.get("type") == "session_meta"), {})
        if not _is_project_path(meta.get("cwd", ""), project):
            continue
        prompts = []
        commands = []
        for event in events:
            payload = event.get("payload", {})
            if event.get("type") == "event_msg" and payload.get("type") == "user_message":
                prompts.append(_brief(str(payload.get("message", ""))))
            if event.get("type") == "response_item" and payload.get("type") == "custom_tool_call":
                name = payload.get("name")
                if name:
                    commands.append(str(name))
        if prompts:
            records.append(SessionSummary("codex", _timestamp(path.stat().st_mtime), prompts[0], prompts[-2:], commands[-3:]))
    return records


def _opencode_sessions(project: Path, since: datetime, home: Path) -> list[SessionSummary]:
    database = home / ".local" / "share" / "opencode" / "opencode.db"
    if not database.is_file():
        return []
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        threshold_ms = int(since.timestamp() * 1000)
        rows = connection.execute(
            "SELECT id, title, time_updated FROM session WHERE directory = ? AND time_updated >= ?",
            (str(project), threshold_ms),
        ).fetchall()
        records = []
        for session_id, title, updated_ms in rows:
            rows = connection.execute(
                "SELECT m.data, p.data FROM message m JOIN part p ON p.message_id = m.id "
                "WHERE p.session_id = ? ORDER BY p.time_created",
                (session_id,),
            ).fetchall()
            prompts = []
            commands = []
            for message_raw, part_raw in rows:
                try:
                    message = json.loads(message_raw)
                    part = json.loads(part_raw)
                except json.JSONDecodeError:
                    continue
                if message.get("role") == "user" and part.get("type") == "text":
                    prompts.append(_brief(str(part.get("text", ""))))
                if part.get("type") == "tool":
                    command = part.get("state", {}).get("input", {}).get("command")
                    if command:
                        commands.append(_brief(str(command), 180))
            records.append(
                SessionSummary("opencode", _timestamp(updated_ms / 1000), _brief(title), prompts[-2:], commands[-3:])
            )
        connection.close()
        return records
    except sqlite3.Error:
        return []


def _transcript_sessions(project: Path, since: datetime, home: Path, source: str) -> list[SessionSummary]:
    if source == "cursor":
        base = home / ".cursor" / "projects"
        pattern = "*/agent-transcripts/**/*.jsonl"
    else:
        base = home / ".config" / "Code" / "User" / "workspaceStorage"
        pattern = "*/GitHub.copilot-chat/transcripts/*.jsonl"
    if not base.is_dir():
        return []
    records = []
    project_text = str(project)
    project_slug = project_text.strip("/").replace("/", "-").replace("_", "-").lower()
    for path in base.glob(pattern):
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < since:
            continue
        contents = path.read_text(errors="replace")
        if project_text not in contents and project_slug not in str(path).replace("_", "-").lower():
            continue
        prompts = []
        for event in _json_lines(path):
            if source == "cursor" and event.get("role") == "user":
                message = event.get("message", {})
                content = message.get("content", []) if isinstance(message, dict) else message
                if isinstance(content, list):
                    prompts.extend(str(item.get("text", "")) for item in content if isinstance(item, dict))
                else:
                    prompts.append(str(content))
            if source == "copilot" and event.get("type") == "user.message":
                prompts.append(str(event.get("data", {}).get("content", "")))
        prompts = [_brief(prompt) for prompt in prompts if prompt.strip()]
        if prompts:
            records.append(SessionSummary(source, _timestamp(path.stat().st_mtime), prompts[0], prompts[-2:], []))
    return records


def collect_sessions(
    project: Path,
    days: int = 7,
    sources: Iterable[str] = SOURCES,
    home: Path | None = None,
) -> list[SessionSummary]:
    """Collect selected local-agent records updated during the requested window."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    home = home or Path.home()
    selected = set(sources)
    records: list[SessionSummary] = []
    if "codex" in selected:
        records.extend(_codex_sessions(project, since, home))
    if "opencode" in selected:
        records.extend(_opencode_sessions(project, since, home))
    if "cursor" in selected:
        records.extend(_transcript_sessions(project, since, home, "cursor"))
    if "copilot" in selected:
        records.extend(_transcript_sessions(project, since, home, "copilot"))
    return sorted(records, key=lambda record: record.updated_at, reverse=True)


def handoff(project: Path, days: int, sources: Iterable[str], limit: int) -> dict[str, Any]:
    """Combine current workspace state with a bounded session-history summary."""
    records = collect_sessions(project, days, sources)[:limit]
    return {
        "context": collect_context(project),
        "session_window_days": days,
        "sessions": [asdict(record) for record in records],
    }


def format_handoff(data: dict[str, Any]) -> str:
    """Render a short continuation brief rather than a raw transcript dump."""
    context = data["context"]
    lines = [
        f"Project handoff: {context['project']}",
        f"Branch: {context['status']['branch']} ({context['head']})",
        f"Current changes: {len(context['status']['changes'])}",
        f"Recent sessions ({data['session_window_days']} days): {len(data['sessions'])}",
    ]
    for session in data["sessions"]:
        lines.append(f"\n- [{session['source']}] {session['updated_at']}: {session['title'][:180]}")
        if session["commands"]:
            lines.append("  Commands: " + "; ".join(session["commands"]))
        for prompt in session["prompts"][-1:]:
            if prompt != session["title"]:
                lines.append("  Latest: " + prompt[:240])
    return "\n".join(lines)
