import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from flexiv_steward.errors import ProjectError
from flexiv_steward.preset_store import current_path, history_path
from flexiv_steward.presets import (
    matching_build_preset,
    remember_preset,
    remembered_configure_preset,
    resolve_build_preset,
    resolve_configure_preset,
)


def _presets(
    configure: list[dict[str, object]], build: list[dict[str, object]]
) -> dict[str, list[dict[str, object]]]:
    return {"configurePresets": configure, "buildPresets": build}


class PresetResolutionTests(unittest.TestCase):
    """Preset defaults for ``fs setup``/``fs build`` without an explicit preset."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tempdir.name)
        subprocess.run(["git", "init"], cwd=self.project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.project, check=True)
        (self.project / "README.md").write_text("test")
        subprocess.run(["git", "add", "README.md"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.project, check=True, capture_output=True)
        self._write_presets()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_presets(
        self,
        configure: list[dict[str, object]] | None = None,
        build: list[dict[str, object]] | None = None,
    ) -> None:
        source = self.project / "flexiv"
        source.mkdir(exist_ok=True)
        (source / "CMakePresets.json").write_text(json.dumps(_presets(
            configure if configure is not None else [
                {
                    "name": "base",
                    "hidden": True,
                    "binaryDir": "${sourceDir}/build",
                    "cacheVariables": {"CMAKE_TOOLCHAIN_FILE": "cmake/yocto-toolchain.cmake"},
                },
                {"name": "yocto-relwithdebinfo", "inherits": "base"},
                {"name": "ubuntu-relwithdebinfo", "binaryDir": "${sourceDir}/build/ubuntu"},
            ],
            build if build is not None else [
                {"name": "yocto-build", "configurePreset": "yocto-relwithdebinfo"},
                {"name": "ubuntu-relwithdebinfo", "configurePreset": "ubuntu-relwithdebinfo"},
            ],
        )))

    def _write_history(self, records: dict[str, int]) -> Path:
        path = history_path(self.project)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": 1,
            "presets": {
                name: {"uses": uses, "last_used": "2026-01-01T00:00:00+00:00"}
                for name, uses in records.items()
            },
        }))
        return path

    def _write_current(
        self, configure_preset: str, preset: str | None = None,
        recorded_at: str = "2026-02-02T00:00:00+00:00",
    ) -> Path:
        path = current_path(self.project)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": 1,
            "configure_preset": configure_preset,
            "preset": preset,
            "recorded_at": recorded_at,
        }))
        return path

    def test_explicit_argument_beats_remembered_and_history(self) -> None:
        self._write_current("yocto-relwithdebinfo", "yocto-build")
        self._write_history({"ubuntu-relwithdebinfo": 9})

        choice = resolve_configure_preset(self.project, "ubuntu-relwithdebinfo")

        self.assertEqual(choice.preset.name, "ubuntu-relwithdebinfo")
        self.assertEqual(choice.origin, "argument")
        self.assertEqual(choice.notes, ())

    def test_remembered_configure_preset_beats_workspace_history(self) -> None:
        self._write_current("yocto-relwithdebinfo", "yocto-build")
        self._write_history({"ubuntu-relwithdebinfo": 9})

        choice = resolve_configure_preset(self.project, None)

        self.assertEqual(choice.origin, "worktree")
        self.assertEqual(choice.preset.name, "yocto-relwithdebinfo")
        self.assertEqual(choice.preset.platform, "yocto")
        self.assertEqual(choice.notes, ())

    def test_history_defaults_to_the_most_used_configure_preset(self) -> None:
        self._write_history({"yocto-relwithdebinfo": 2, "ubuntu-relwithdebinfo": 5})

        choice = resolve_configure_preset(self.project, None)

        self.assertEqual(choice.origin, "history")
        self.assertEqual(choice.preset.name, "ubuntu-relwithdebinfo")

    def test_history_skips_names_that_are_not_configure_presets(self) -> None:
        self._write_history({"deleted-preset": 4, "yocto-build": 3, "ubuntu-relwithdebinfo": 1})

        choice = resolve_configure_preset(self.project, None)

        self.assertEqual((choice.origin, choice.preset.name), ("history", "ubuntu-relwithdebinfo"))

    def test_stale_remembered_configure_preset_is_ignored_with_a_note(self) -> None:
        self._write_current("deleted-preset", "deleted-build")
        self._write_history({"ubuntu-relwithdebinfo": 1})

        choice = resolve_configure_preset(self.project, None)

        self.assertEqual(choice.origin, "history")
        self.assertEqual(choice.preset.name, "ubuntu-relwithdebinfo")
        self.assertIn("ignoring stale remembered preset 'deleted-preset'", choice.notes)

    def test_hidden_remembered_configure_preset_is_not_reused(self) -> None:
        self._write_current("base")

        with self.assertRaisesRegex(ProjectError, "fs preset list"):
            resolve_configure_preset(self.project, None)

    def test_no_configure_default_anywhere_explains_how_to_choose(self) -> None:
        with self.assertRaises(ProjectError) as error:
            resolve_configure_preset(self.project, None)

        message = str(error.exception)
        self.assertIn("no remembered or most-used preset", message)
        self.assertIn("fs preset list", message)
        self.assertIn("fs setup <preset>", message)

    def test_build_uses_the_remembered_build_preset(self) -> None:
        self._write_current("yocto-relwithdebinfo", "yocto-build")

        choice = resolve_build_preset(self.project, None)

        self.assertEqual(choice.origin, "worktree")
        self.assertEqual(choice.preset.name, "yocto-build")
        self.assertEqual(choice.preset.configure_name, "yocto-relwithdebinfo")
        self.assertEqual(choice.preset.platform, "yocto")
        self.assertEqual(choice.notes, ())

    def test_build_maps_a_remembered_configure_preset_and_notes_the_mapping(self) -> None:
        self._write_current("yocto-relwithdebinfo")

        choice = resolve_build_preset(self.project, None)

        self.assertEqual((choice.origin, choice.preset.name), ("worktree", "yocto-build"))
        self.assertIn(
            "build preset 'yocto-build' configures remembered preset 'yocto-relwithdebinfo'",
            choice.notes,
        )

    def test_build_mapping_with_the_same_name_needs_no_mapping_note(self) -> None:
        self._write_current("ubuntu-relwithdebinfo")

        choice = resolve_build_preset(self.project, None)

        self.assertEqual((choice.origin, choice.preset.name), ("worktree", "ubuntu-relwithdebinfo"))
        self.assertEqual(choice.notes, ())

    def test_build_falls_back_from_a_stale_build_preset_to_the_configure_mapping(self) -> None:
        self._write_current("yocto-relwithdebinfo", "deleted-build")

        choice = resolve_build_preset(self.project, None)

        self.assertEqual((choice.origin, choice.preset.name), ("worktree", "yocto-build"))
        self.assertIn("ignoring stale remembered build preset 'deleted-build'", choice.notes)

    def test_build_does_not_fall_back_to_workspace_history(self) -> None:
        self._write_history({"yocto-relwithdebinfo": 7})

        with self.assertRaises(ProjectError) as error:
            resolve_build_preset(self.project, None)

        message = str(error.exception)
        self.assertIn(str(current_path(self.project)), message)
        self.assertIn("fs setup <preset>", message)
        self.assertIn("fs preset list", message)

    def test_build_without_a_usable_remembered_build_preset_explains_the_state(self) -> None:
        self._write_current("yocto-relwithdebinfo")
        self._write_presets(build=[{"name": "unrelated", "configurePreset": "ubuntu-relwithdebinfo"}])

        with self.assertRaises(ProjectError) as error:
            resolve_build_preset(self.project, None)

        message = str(error.exception)
        self.assertIn("no usable preset is remembered", message)
        self.assertIn("fs setup <preset>", message)

    def test_build_reports_stale_remembered_configure_preset(self) -> None:
        self._write_current("deleted-preset", "deleted-build")

        with self.assertRaises(ProjectError) as error:
            resolve_build_preset(self.project, None)

        message = str(error.exception)
        self.assertIn("ignoring stale remembered build preset 'deleted-build'", message)

    def test_explicit_unknown_presets_keep_the_existing_errors(self) -> None:
        with self.assertRaisesRegex(ProjectError, "unknown CMake configure preset 'nope'"):
            resolve_configure_preset(self.project, "nope")
        with self.assertRaisesRegex(ProjectError, "unknown CMake build preset 'nope'"):
            resolve_build_preset(self.project, "nope")

    def test_matching_build_preset_prefers_the_same_name(self) -> None:
        self._write_presets(build=[
            {"name": "first-build", "configurePreset": "ubuntu-relwithdebinfo"},
            {"name": "ubuntu-relwithdebinfo", "configurePreset": "ubuntu-relwithdebinfo"},
        ])

        self.assertEqual(
            matching_build_preset(self.project, "ubuntu-relwithdebinfo"), "ubuntu-relwithdebinfo"
        )

    def test_matching_build_preset_falls_back_to_file_order(self) -> None:
        self._write_presets(build=[
            {"name": "zeta-build", "configurePreset": "ubuntu-relwithdebinfo"},
            {"name": "alpha-build", "configurePreset": "ubuntu-relwithdebinfo"},
        ])

        self.assertEqual(matching_build_preset(self.project, "ubuntu-relwithdebinfo"), "zeta-build")

    def test_matching_build_preset_skips_hidden_and_unknown_names(self) -> None:
        self._write_presets(
            configure=[
                {"name": "base", "hidden": True},
                {"name": "ubuntu-relwithdebinfo"},
            ],
            build=[
                {"name": "hidden-build", "hidden": True, "configurePreset": "ubuntu-relwithdebinfo"},
                {"name": "ubuntu-relwithdebinfo", "configurePreset": "ubuntu-relwithdebinfo"},
            ],
        )

        self.assertEqual(
            matching_build_preset(self.project, "ubuntu-relwithdebinfo"), "ubuntu-relwithdebinfo"
        )
        self.assertIsNone(matching_build_preset(self.project, "base"))
        self.assertIsNone(matching_build_preset(self.project, "deleted-preset"))

    def test_matching_build_preset_resolves_inherited_configure_mapping(self) -> None:
        self._write_presets(
            configure=[
                {"name": "base", "hidden": True},
                {"name": "ubuntu-relwithdebinfo", "inherits": "base"},
            ],
            build=[
                {"name": "base-build", "hidden": True, "configurePreset": "base"},
                {"name": "inherited-build", "inherits": "base-build",
                 "configurePreset": "ubuntu-relwithdebinfo"},
            ],
        )

        self.assertEqual(matching_build_preset(self.project, "ubuntu-relwithdebinfo"), "inherited-build")

    def test_remember_preset_accepts_a_configure_name_and_maps_its_build(self) -> None:
        preset, build = remember_preset(self.project, "ubuntu-relwithdebinfo")

        self.assertEqual(preset.name, "ubuntu-relwithdebinfo")
        self.assertEqual(preset.platform, "ubuntu")
        self.assertEqual(preset.configure_name, "ubuntu-relwithdebinfo")
        self.assertEqual(build, "ubuntu-relwithdebinfo")

    def test_remember_preset_maps_a_differently_named_build_preset(self) -> None:
        preset, build = remember_preset(self.project, "yocto-relwithdebinfo")

        self.assertEqual(preset.name, "yocto-relwithdebinfo")
        self.assertEqual(preset.platform, "yocto")
        self.assertEqual(build, "yocto-build")

    def test_remember_preset_returns_no_build_preset_when_none_matches(self) -> None:
        self._write_presets(build=[])

        preset, build = remember_preset(self.project, "ubuntu-relwithdebinfo")

        self.assertEqual(preset.name, "ubuntu-relwithdebinfo")
        self.assertIsNone(build)

    def test_remember_preset_accepts_a_build_name_and_stores_its_configure_preset(self) -> None:
        preset, build = remember_preset(self.project, "yocto-build")

        self.assertEqual(preset.name, "yocto-relwithdebinfo")
        self.assertEqual(preset.platform, "yocto")
        self.assertEqual(build, "yocto-build")

    def test_remember_preset_rejects_unknown_and_hidden_names(self) -> None:
        with self.assertRaisesRegex(ProjectError, "unknown CMake preset 'nope'"):
            remember_preset(self.project, "nope")
        with self.assertRaises(ProjectError) as error:
            remember_preset(self.project, "base")
        self.assertIn("fs preset list", str(error.exception))

        self._write_presets(build=[
            {"name": "hidden-build", "hidden": True, "configurePreset": "ubuntu-relwithdebinfo"},
        ])
        with self.assertRaises(ProjectError) as hidden:
            remember_preset(self.project, "hidden-build")
        self.assertIn("fs preset list", str(hidden.exception))

    def test_remembered_configure_preset_is_absent_without_a_record(self) -> None:
        self.assertEqual(remembered_configure_preset(self.project), (None, ()))

    def test_remembered_configure_preset_returns_the_ubuntu_preset(self) -> None:
        self._write_current("ubuntu-relwithdebinfo")

        preset, notes = remembered_configure_preset(self.project)

        self.assertIsNotNone(preset)
        assert preset is not None
        self.assertEqual(preset.name, "ubuntu-relwithdebinfo")
        self.assertEqual(preset.platform, "ubuntu")
        self.assertEqual(notes, ())

    def test_remembered_stale_configure_preset_is_skipped_with_a_note(self) -> None:
        self._write_current("deleted-preset")

        self.assertEqual(
            remembered_configure_preset(self.project),
            (None, ("ignoring stale remembered preset 'deleted-preset'",)),
        )

    def test_remembered_yocto_configure_preset_is_rejected_with_a_note(self) -> None:
        self._write_current("yocto-relwithdebinfo")

        preset, notes = remembered_configure_preset(self.project)

        self.assertIsNone(preset)
        self.assertEqual(notes, (
            "remembered preset 'yocto-relwithdebinfo' is not an Ubuntu build; "
            "using the default build directory",
        ))

    def test_remembered_hidden_configure_preset_is_skipped_as_stale(self) -> None:
        self._write_current("base")

        self.assertEqual(
            remembered_configure_preset(self.project),
            (None, ("ignoring stale remembered preset 'base'",)),
        )
