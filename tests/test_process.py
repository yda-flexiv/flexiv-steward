import sys
import tempfile
import unittest
from pathlib import Path

from flexiv_steward.errors import ProjectError
from flexiv_steward.process import run_process


class ProcessTests(unittest.TestCase):
    def test_captures_output_and_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            result = run_process(
                [sys.executable, "-c", "print('ready')"],
                Path(tempdir),
                check=True,
            )

        self.assertEqual(result.stdout.strip(), "ready")
        self.assertEqual(result.returncode, 0)
        self.assertGreaterEqual(result.duration_seconds, 0)

    def test_normalizes_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with self.assertLogs("flexiv_steward.process", level="WARNING"):
                with self.assertRaisesRegex(ProjectError, "timed out"):
                    run_process(
                        [sys.executable, "-c", "import time; time.sleep(1)"],
                        Path(tempdir),
                        timeout=0.01,
                    )
