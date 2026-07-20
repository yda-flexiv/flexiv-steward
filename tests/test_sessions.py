import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from flexiv_steward.sessions import _brief, _codex_sessions, _redact


class SessionTests(unittest.TestCase):
    def test_redacts_common_credential_assignments(self) -> None:
        self.assertEqual(_redact("api_key=very-secret-value"), "api_key=<redacted>")
        self.assertEqual(_brief("x" * 300), "x" * 239 + "…")

    def test_reads_codex_sessions_for_the_requested_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir)
            project = home / "project"
            project.mkdir()
            session_file = home / ".codex" / "sessions" / "2026" / "07" / "20" / "session.jsonl"
            session_file.parent.mkdir(parents=True)
            session_file.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session_meta", "payload": {"cwd": str(project)}}),
                        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "inspect api_key=abc"}}),
                    ]
                )
            )
            records = _codex_sessions(project, datetime(2026, 1, 1, tzinfo=timezone.utc), home)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].title, "inspect api_key=<redacted>")
