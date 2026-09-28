import tempfile
import unittest
from pathlib import Path

from flexiv_steward.deps import declared_externals, has_blocking_errors, has_dependency_errors
from flexiv_steward.formatters import format_dependency_doctor
from flexiv_steward.models import DependencyStatus, ExternalSpec


class DependencyTests(unittest.TestCase):
    def test_reads_external_tag_and_source_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            project = Path(tempdir)
            cmake = project / "flexiv" / "CMakeLists.txt"
            cmake.parent.mkdir()
            cmake.write_text(
                "FlexivAddExternal(flexiv_sw_proto\n"
                "  GIT_URL git@example/proto.git\n"
                "  GIT_TAG 1bcdef0123456789\n"
                "  SOURCE_DIR ${CMAKE_SOURCE_DIR}/external/proto\n"
                ")\n"
            )
            self.assertEqual(
                declared_externals(project),
                [
                    ExternalSpec(
                        "flexiv_sw_proto",
                        project / "flexiv" / "external" / "proto",
                        "git@example/proto.git",
                        "1bcdef0123456789",
                    )
                ],
            )

    def test_marks_only_version_drift_as_strict_error(self) -> None:
        statuses = [
            DependencyStatus("external", "proto", "/tmp/proto", "1bc", "222", "mismatch"),
            DependencyStatus("external", "base", "/tmp/base", "abc", "abc", "ok", dirty=True),
        ]
        self.assertTrue(has_dependency_errors(statuses))
        self.assertTrue(has_blocking_errors(statuses))
        output = format_dependency_doctor(statuses)
        self.assertIn("Do not build", output)
        self.assertIn("fs setup <preset>", output)

    def test_dirty_mismatch_only_warns_instead_of_blocking(self) -> None:
        statuses = [
            DependencyStatus("external", "base", "/tmp/base", "720", "9a0", "mismatch", dirty=True),
        ]
        self.assertTrue(has_dependency_errors(statuses))
        self.assertFalse(has_blocking_errors(statuses))
        output = format_dependency_doctor(statuses)
        self.assertNotIn("Do not build", output)
        self.assertIn("override the declared revisions", output)

    def test_missing_and_conflicted_always_block(self) -> None:
        for state in ("missing", "uninitialized", "conflicted"):
            statuses = [DependencyStatus("submodule", "tools", "/tmp/tools", None, None, state, dirty=True)]
            self.assertTrue(has_blocking_errors(statuses), state)

    def test_compacts_clean_output_unless_verbose(self) -> None:
        statuses = [DependencyStatus("submodule", "proto", "/tmp/proto", "abc", "abc", "ok")]
        self.assertNotIn("DEPENDENCY", format_dependency_doctor(statuses))
        self.assertIn("DEPENDENCY", format_dependency_doctor(statuses, verbose=True))

    def test_verbose_table_aligns_long_dependency_names(self) -> None:
        statuses = [
            DependencyStatus(
                "submodule",
                "flexiv/lib/flexiv_simulation/thirdparty/flexiv_joint_control/cmake/tools",
                "/tmp/tools",
                "065a4fc730c8",
                "065a4fc730c8",
                "ok",
            ),
            DependencyStatus("external", "proto", "/tmp/proto", "21171bfdfec8", "21171bfdfec8", "ok"),
        ]

        output = format_dependency_doctor(statuses, verbose=True)
        first = next(line for line in output.splitlines() if "cmake/tools" in line)
        second = next(line for line in output.splitlines() if "proto" in line)

        self.assertIn("DEPENDENCY", output)
        self.assertEqual(first.index("065a4fc730c8"), second.index("21171bfdfec8"))

    def test_reads_external_from_direct_source_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            project = Path(tempdir)
            (project / "CMakeLists.txt").write_text(
                "FlexivAddExternal(flexiv_sw_proto\n"
                "  GIT_URL git@example/proto.git\n"
                "  GIT_TAG 1bcdef0123456789\n"
                "  SOURCE_DIR ${CMAKE_SOURCE_DIR}/external/proto\n"
                ")\n"
            )

            self.assertEqual(
                declared_externals(project),
                [
                    ExternalSpec(
                        "flexiv_sw_proto",
                        project / "external" / "proto",
                        "git@example/proto.git",
                        "1bcdef0123456789",
                    )
                ],
            )
