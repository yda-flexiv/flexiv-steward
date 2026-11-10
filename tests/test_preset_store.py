"""Tests for preset_store: workspace history and per-worktree preset state."""

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flexiv_steward.errors import ProjectError
from flexiv_steward.preset_store import (
    CURRENT_FILE,
    HISTORY_FILE,
    IGNORE_ENTRY,
    SCHEMA_VERSION,
    STEWARD_DIRECTORY,
    PresetUsage,
    WorktreePreset,
    current_path,
    ensure_ignored,
    history_path,
    load_current,
    load_history,
    preferred_preset,
    record_usage,
    store_current,
    workspace_of,
)

UTC = timezone.utc


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


class PresetStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        probe = subprocess.run(
            ["git", "rev-parse", "--git-dir"], cwd=self.root, capture_output=True, text=True
        )
        if probe.returncode == 0:
            self.tempdir.cleanup()
            self.skipTest("temporary directory unexpectedly sits inside a Git repository")
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _init_repo(self, path: Path) -> Path:
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=path, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True
        )
        (path / "README.md").write_text("test", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-qm", "initial"], cwd=path, check=True, capture_output=True
        )
        return path

    def _add_linked_worktree(self, repo: Path, branch: str, name: str) -> Path:
        destination = self.root / name
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", branch, str(destination)],
            cwd=repo, check=True, capture_output=True,
        )
        return destination

    def _git_status(self, path: Path) -> str:
        return subprocess.run(
            ["git", "status", "--porcelain"], cwd=path, check=True, capture_output=True, text=True
        ).stdout

    def test_history_round_trip_counts_uses_and_writes_utf8_json(self) -> None:
        name = "调试-预设"
        record_usage(self.workspace, name)
        record_usage(self.workspace, name)
        record_usage(self.workspace, "ubuntu-static-check")

        self.assertEqual(
            history_path(self.workspace),
            self.workspace / STEWARD_DIRECTORY / HISTORY_FILE,
        )
        usages = load_history(self.workspace)
        self.assertEqual([usage.name for usage in usages], [name, "ubuntu-static-check"])
        self.assertEqual([usage.uses for usage in usages], [2, 1])

        # The file must be UTF-8 JSON and keep the non-ASCII preset name intact.
        raw = history_path(self.workspace).read_text(encoding="utf-8")
        payload = json.loads(raw)
        self.assertEqual(payload["version"], SCHEMA_VERSION)
        self.assertEqual(set(payload["presets"]), {name, "ubuntu-static-check"})
        self.assertEqual(payload["presets"][name], {"uses": 2, "last_used": usages[0].last_used})
        self.assertEqual(payload["presets"]["ubuntu-static-check"]["uses"], 1)

    def test_history_orders_by_uses_then_recency_then_name(self) -> None:
        t0 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        t1 = datetime(2024, 1, 1, 1, 0, 0, tzinfo=UTC)
        t2 = datetime(2024, 1, 1, 2, 0, 0, tzinfo=UTC)
        record_usage(self.workspace, "frequent", now=t0)
        record_usage(self.workspace, "frequent", now=t2)
        record_usage(self.workspace, "recent", now=t2)
        record_usage(self.workspace, "aaa", now=t1)
        record_usage(self.workspace, "zzz", now=t1)
        record_usage(self.workspace, "old", now=t0)

        usages = load_history(self.workspace)
        self.assertEqual(
            [usage.name for usage in usages], ["frequent", "recent", "aaa", "zzz", "old"]
        )
        self.assertEqual(
            [usage.uses for usage in usages], [2, 1, 1, 1, 1]
        )
        self.assertEqual(
            [usage.last_used for usage in usages],
            [
                _iso(t2),
                _iso(t2),
                _iso(t1),
                _iso(t1),
                _iso(t0),
            ],
        )

    def test_record_usage_normalizes_timestamps_to_utc_seconds(self) -> None:
        moment = datetime(2024, 3, 4, 5, 6, 7, 987654, tzinfo=timezone(timedelta(hours=9)))
        record_usage(self.workspace, "cfg", now=moment)
        self.assertEqual(load_history(self.workspace)[0].last_used, "2024-03-03T20:06:07+00:00")

        record_usage(self.workspace, "default-clock")
        default_usage = next(
            usage for usage in load_history(self.workspace) if usage.name == "default-clock"
        )
        parsed = datetime.fromisoformat(default_usage.last_used)
        self.assertEqual(parsed.utcoffset(), timedelta(0))
        self.assertLess(abs((datetime.now(UTC) - parsed).total_seconds()), 60)

    def test_preferred_preset_is_none_without_history_and_top_name_otherwise(self) -> None:
        self.assertIsNone(preferred_preset(self.workspace))

        path = history_path(self.workspace)
        path.parent.mkdir(parents=True)
        path.write_text("{}", encoding="utf-8")
        self.assertIsNone(preferred_preset(self.workspace))
        path.write_text('{"version": 1, "presets": {}}', encoding="utf-8")
        self.assertIsNone(preferred_preset(self.workspace))
        path.write_text("not json", encoding="utf-8")
        self.assertIsNone(preferred_preset(self.workspace))

        t0 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        record_usage(self.workspace, "second", now=t0)
        record_usage(self.workspace, "first", now=t0)
        record_usage(self.workspace, "first", now=t0)
        self.assertEqual(preferred_preset(self.workspace), "first")

    def test_corrupt_history_is_ignored_and_rewritten_by_record_usage(self) -> None:
        cases = {
            "invalid json": "not json",
            "list root": "[]",
            "presets is a list": '{"version": 1, "presets": []}',
            "uses is a string": (
                '{"version": 1, "presets": {"preset-a": {"uses": "3",'
                ' "last_used": "2024-01-01T00:00:00+00:00"}}}'
            ),
            "uses is a bool field": '{"presets": {"preset-a": {"uses": true}}}',
            "bool record": '{"presets": {"preset-a": true}}',
            "zero uses": '{"presets": {"preset-a": {"uses": 0}}}',
            "negative uses": '{"presets": {"preset-a": -2}}',
            "empty file": "",
        }
        moment = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
        path = history_path(self.workspace)
        for label, content in cases.items():
            with self.subTest(label=label):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                self.assertEqual(load_history(self.workspace), ())

                record_usage(self.workspace, "recovered", now=moment)

                self.assertEqual(
                    load_history(self.workspace),
                    (PresetUsage("recovered", 1, "2024-01-02T03:04:05+00:00"),),
                )
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["version"], SCHEMA_VERSION)
                self.assertEqual(
                    payload["presets"],
                    {"recovered": {"uses": 1, "last_used": "2024-01-02T03:04:05+00:00"}},
                )

    def test_legacy_flat_mapping_is_read_as_uses(self) -> None:
        path = history_path(self.workspace)
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "presets": {"preset-a": 3, "preset-b": 0, "preset-c": -2, "  ": 4},
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            load_history(self.workspace), (PresetUsage("preset-a", 3, ""),)
        )

        # A legacy counter is still the starting point for the next use.
        record_usage(self.workspace, "preset-a", now=datetime(2024, 1, 1, tzinfo=UTC))
        self.assertEqual(
            load_history(self.workspace),
            (PresetUsage("preset-a", 4, "2024-01-01T00:00:00+00:00"),),
        )

    def test_current_preset_round_trip(self) -> None:
        moment = datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)
        store_current(
            self.project,
            configure_preset="ubuntu-static-check",
            preset="ubuntu-static-check",
            now=moment,
        )

        self.assertEqual(
            current_path(self.project), self.project / STEWARD_DIRECTORY / CURRENT_FILE
        )
        self.assertEqual(
            load_current(self.project),
            WorktreePreset(
                "ubuntu-static-check", "ubuntu-static-check", "2024-05-06T07:08:09+00:00"
            ),
        )
        payload = json.loads(current_path(self.project).read_text(encoding="utf-8"))
        self.assertEqual(
            payload,
            {
                "version": SCHEMA_VERSION,
                "configure_preset": "ubuntu-static-check",
                "preset": "ubuntu-static-check",
                "recorded_at": "2024-05-06T07:08:09+00:00",
            },
        )

    def test_current_preset_missing_or_stale_keys_yield_none(self) -> None:
        path = current_path(self.project)
        cases = [
            ("absent file", None, None),
            ("empty file", "", None),
            ("invalid json", "not json", None),
            ("list root", "[]", None),
            ("empty mapping", "{}", None),
            ("blank configure preset", '{"configure_preset": ""}', None),
            ("whitespace configure preset", '{"configure_preset": "   "}', None),
            ("non-string configure preset", '{"configure_preset": 42}', None),
            (
                "trimmed names",
                '{"configure_preset": " cfg ", "preset": " bld ",'
                ' "recorded_at": "2024-01-01T00:00:00+00:00"}',
                WorktreePreset("cfg", "bld", "2024-01-01T00:00:00+00:00"),
            ),
            ("absent optional keys", '{"configure_preset": "cfg"}', WorktreePreset("cfg", None, "")),
            (
                "blank build preset",
                '{"configure_preset": "cfg", "preset": "  ", "recorded_at": 7}',
                WorktreePreset("cfg", None, ""),
            ),
            (
                "non-string build preset",
                '{"configure_preset": "cfg", "preset": 5, "recorded_at": "stamp"}',
                WorktreePreset("cfg", None, "stamp"),
            ),
            (
                "null build preset",
                '{"configure_preset": "cfg", "preset": null, "recorded_at": "stamp"}',
                WorktreePreset("cfg", None, "stamp"),
            ),
        ]
        for label, content, expected in cases:
            with self.subTest(label=label):
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                self.assertEqual(load_current(self.project), expected)

    def test_store_current_without_build_preset_keeps_configure_preset(self) -> None:
        first = datetime(2024, 1, 1, tzinfo=UTC)
        second = datetime(2024, 1, 2, tzinfo=UTC)
        store_current(self.project, configure_preset="cfg", preset="bld", now=first)
        store_current(self.project, configure_preset="cfg", preset=None, now=second)
        self.assertEqual(
            load_current(self.project),
            WorktreePreset("cfg", None, "2024-01-02T00:00:00+00:00"),
        )

        store_current(self.project, configure_preset="cfg", preset="   ", now=second)
        self.assertEqual(
            load_current(self.project),
            WorktreePreset("cfg", None, "2024-01-02T00:00:00+00:00"),
        )

    def test_empty_preset_names_are_rejected(self) -> None:
        for blank in ("", "   ", "\t\n"):
            with self.subTest(blank=blank):
                with self.assertRaisesRegex(ProjectError, "empty preset name"):
                    record_usage(self.workspace, blank)
                with self.assertRaisesRegex(ProjectError, "empty preset name"):
                    store_current(self.project, configure_preset=blank, preset="bld")
        self.assertFalse(history_path(self.workspace).exists())
        self.assertFalse(current_path(self.project).exists())

    def test_atomic_writes_leave_no_temporary_files(self) -> None:
        record_usage(self.workspace, "cfg")
        store_current(self.project, configure_preset="cfg", preset="bld")

        self.assertEqual(
            sorted(path.name for path in (self.workspace / STEWARD_DIRECTORY).iterdir()),
            [HISTORY_FILE],
        )
        self.assertEqual(
            sorted(path.name for path in (self.project / STEWARD_DIRECTORY).iterdir()),
            [CURRENT_FILE],
        )
        for directory in (self.workspace / STEWARD_DIRECTORY, self.project / STEWARD_DIRECTORY):
            self.assertEqual(
                [path.name for path in directory.iterdir() if path.name.endswith(".tmp")], []
            )

    def test_ensure_ignored_is_idempotent_and_keeps_state_out_of_git_status(self) -> None:
        repo = self._init_repo(self.root / "repo")
        exclude = repo / ".git" / "info" / "exclude"

        ensure_ignored(repo)
        ensure_ignored(repo)

        content = exclude.read_text(encoding="utf-8")
        self.assertEqual(content.count(IGNORE_ENTRY), 1)
        self.assertEqual(
            [line for line in content.splitlines() if line.strip() == IGNORE_ENTRY],
            [IGNORE_ENTRY],
        )

        store_current(repo, configure_preset="cfg", preset="bld")
        record_usage(repo, "cfg")
        self.assertTrue((repo / STEWARD_DIRECTORY / CURRENT_FILE).is_file())
        self.assertTrue((repo / STEWARD_DIRECTORY / HISTORY_FILE).is_file())
        self.assertEqual(self._git_status(repo), "")

    def test_ensure_ignored_raises_outside_git_but_state_still_works(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()

        with self.assertRaises(ProjectError):
            ensure_ignored(plain)

        moment = datetime(2024, 1, 1, tzinfo=UTC)
        store_current(plain, configure_preset="cfg", preset="bld", now=moment)
        record_usage(plain, "cfg", now=moment)
        self.assertEqual(
            load_current(plain), WorktreePreset("cfg", "bld", "2024-01-01T00:00:00+00:00")
        )
        self.assertEqual(preferred_preset(plain), "cfg")
        self.assertFalse((plain / "info").exists())

    def test_ensure_ignored_from_a_linked_worktree_covers_that_worktree(self) -> None:
        repo = self._init_repo(self.root / "repo")
        linked = self._add_linked_worktree(repo, "task/SW-1", "repo-task-SW-1")

        ensure_ignored(linked)
        store_current(linked, configure_preset="cfg", preset="bld")

        self.assertTrue((linked / STEWARD_DIRECTORY / CURRENT_FILE).is_file())
        self.assertEqual(self._git_status(linked), "")
        exclude = repo / ".git" / "info" / "exclude"
        self.assertIn(IGNORE_ENTRY, exclude.read_text(encoding="utf-8").splitlines())

    def test_workspace_of_resolves_the_primary_worktree(self) -> None:
        repo = self._init_repo(self.root / "repo")
        self.assertEqual(workspace_of(repo).resolve(), repo.resolve())

        linked = self._add_linked_worktree(repo, "task/SW-2", "repo-task-SW-2")
        self.assertEqual(workspace_of(linked).resolve(), repo.resolve())

        # History recorded through a linked worktree belongs to the workspace.
        record_usage(workspace_of(linked), "cfg", now=datetime(2024, 1, 1, tzinfo=UTC))
        self.assertEqual(preferred_preset(repo), "cfg")
        self.assertTrue((repo / STEWARD_DIRECTORY / HISTORY_FILE).is_file())

    def test_workspace_of_a_bare_hub_returns_the_hub_directory(self) -> None:
        seed = self._init_repo(self.root / "seed")
        hub = self.root / "hub"
        hub.mkdir()
        bare = hub / ".bare"
        subprocess.run(
            ["git", "clone", "-q", "--bare", str(seed), str(bare)],
            check=True, capture_output=True,
        )
        worktree = hub / "task-SW-9"
        subprocess.run(
            ["git", "-C", str(bare), "worktree", "add", "-q", str(worktree)],
            check=True, capture_output=True,
        )
        moment = datetime(2024, 1, 1, tzinfo=UTC)

        workspace = workspace_of(worktree)
        self.assertEqual(workspace.resolve(), hub.resolve())
        self.assertEqual(history_path(workspace), hub / STEWARD_DIRECTORY / HISTORY_FILE)

        record_usage(workspace, "cfg", now=moment)
        store_current(worktree, configure_preset="cfg", preset="bld", now=moment)

        # Workspace history is shared outside every worktree; the per-worktree
        # state lives inside the worktree and both stay out of `git status`.
        self.assertTrue((hub / STEWARD_DIRECTORY / HISTORY_FILE).is_file())
        self.assertTrue((worktree / STEWARD_DIRECTORY / CURRENT_FILE).is_file())
        self.assertFalse((bare / STEWARD_DIRECTORY).exists())
        self.assertEqual(
            load_current(worktree),
            WorktreePreset("cfg", "bld", "2024-01-01T00:00:00+00:00"),
        )
        self.assertEqual(self._git_status(worktree), "")
        self.assertIn(
            IGNORE_ENTRY, (bare / "info" / "exclude").read_text(encoding="utf-8").splitlines()
        )

    def test_state_writes_stay_inside_the_given_temporary_directories(self) -> None:
        original_cwd = Path.cwd()
        record_usage(self.workspace, "cfg")
        store_current(self.project, configure_preset="cfg", preset="bld")

        self.assertEqual(Path.cwd(), original_cwd)
        self.assertEqual(
            sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")),
            [
                "project",
                "project/.steward",
                "project/.steward/preset.json",
                "workspace",
                "workspace/.steward",
                "workspace/.steward/preset_history.json",
            ],
        )
        self.assertFalse((self.root / STEWARD_DIRECTORY).exists())
