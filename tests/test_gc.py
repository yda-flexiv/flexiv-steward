import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from flexiv_steward.formatters import format_gc
from flexiv_steward.gc import collect_gc_report
from flexiv_steward.models import WorktreeInfo


class GarbageCollectionTests(unittest.TestCase):
    def test_dry_run_report_is_sorted_and_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            stale = root / "stale"
            active = root / "active"
            (stale / "build").mkdir(parents=True)
            (active / "build").mkdir(parents=True)
            old_time = datetime.now(timezone.utc) - timedelta(days=60)
            old = old_time.isoformat(timespec="seconds")
            recent = datetime.now(timezone.utc).isoformat(timespec="seconds")
            os.utime(stale / "build", (old_time.timestamp(), old_time.timestamp()))
            os.utime(active / "build", (old_time.timestamp(), old_time.timestamp()))
            worktrees = (
                WorktreeInfo(stale, "abc", branch="stale", exists=True, changes=0, last_activity=old),
                WorktreeInfo(active, "def", branch="active", exists=True, changes=1, last_activity=recent),
            )

            with (
                patch("flexiv_steward.gc.inspect_worktrees", return_value=worktrees),
                patch("flexiv_steward.gc._directory_size", side_effect=[8 * 1024**3, 12 * 1024**3]),
            ):
                report = collect_gc_report(stale, older_than_days=30)

            self.assertEqual(report.total_bytes, 20 * 1024**3)
            self.assertEqual(report.reclaimable_bytes, 8 * 1024**3)
            self.assertEqual(report.candidates[0].path, active / "build")
            output = format_gc(report)
            self.assertIn("DRY RUN", output)
            self.assertIn("20.00 GiB", output)
            self.assertIn("8.00 GiB", output)
