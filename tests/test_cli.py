import io
import json
import shlex
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from flexiv_steward.cli import main


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tempdir.name)
        subprocess.run(["git", "init"], cwd=self.project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.project, check=True)
        (self.project / "README.md").write_text("test")
        subprocess.run(["git", "add", "README.md"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.project, check=True, capture_output=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _invoke(self, *argv: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as exit_error:
                main(list(argv))
        return exit_error.exception.code, stdout.getvalue(), stderr.getvalue()

    def test_help_and_parser_errors_have_cli_exit_codes(self) -> None:
        code, stdout, stderr = self._invoke("--help")
        self.assertEqual(code, 0)
        self.assertIn("{context,wt,builds,deps,build,setup,gc,test,session,branchdiff}", stdout)
        self.assertEqual(stderr, "")

        code, stdout, stderr = self._invoke("--not-an-option")
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("error:", stderr)

    def test_context_json_is_machine_readable(self) -> None:
        code, stdout, stderr = self._invoke("--project", str(self.project), "context", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout)["project"], str(self.project))
        self.assertNotIn("\x1b[", stdout)

    def test_context_keeps_the_compact_layout(self) -> None:
        code, stdout, stderr = self._invoke("--project", str(self.project), "context")
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("Project:", stdout)

    def test_gc_requires_dry_run_and_emits_machine_readable_sizes(self) -> None:
        (self.project / ".gitignore").write_text("build/\n")
        (self.project / "build").mkdir()
        (self.project / "build" / "artifact.o").write_bytes(b"x" * 4096)

        code, stdout, stderr = self._invoke(
            "--project", str(self.project), "gc", "--dry-run", "--json"
        )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        report = json.loads(stdout)
        self.assertGreater(report["total_bytes"], 0)
        self.assertEqual(report["candidates"][0]["path"], str(self.project / "build"))

        code, stdout, stderr = self._invoke("--project", str(self.project), "gc")
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("requires --dry-run", stderr)

    def test_build_dry_run_preserves_nested_routing_and_shell_quoting(self) -> None:
        (self.project / "flexiv").mkdir()
        (self.project / "docker").mkdir()
        (self.project / "docker" / "docker_dispatch.sh").touch()
        code, stdout, stderr = self._invoke(
            "--project", str(self.project), "build", "custom", "--preset", "debug; echo unsafe", "--configure", "--dry-run"
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        commands = [shlex.split(line.removeprefix("$ ")) for line in stdout.splitlines()]
        self.assertEqual(commands[0][-1], "cd flexiv && cmake --preset 'debug; echo unsafe'")
        self.assertEqual(commands[1][-1], "cd flexiv && cmake --build --preset 'debug; echo unsafe'")
