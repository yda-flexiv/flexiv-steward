import tempfile
import unittest
from pathlib import Path

from flexiv_steward.deps import DependencyStatus, declared_externals, format_dependency_doctor, has_dependency_errors


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
                [("flexiv_sw_proto", project / "flexiv" / "external" / "proto", "1bcdef0123456789")],
            )

    def test_marks_only_version_drift_as_strict_error(self) -> None:
        statuses = [
            DependencyStatus("external", "proto", "/tmp/proto", "1bc", "222", "mismatch"),
            DependencyStatus("external", "base", "/tmp/base", "abc", "abc", "ok", dirty=True),
        ]
        self.assertTrue(has_dependency_errors(statuses))
        output = format_dependency_doctor(statuses)
        self.assertIn("Do not build", output)
        self.assertIn("fs setup <profile>", output)

    def test_compacts_clean_output_unless_verbose(self) -> None:
        statuses = [DependencyStatus("submodule", "proto", "/tmp/proto", "abc", "abc", "ok")]
        self.assertNotIn("DEPENDENCY", format_dependency_doctor(statuses))
        self.assertIn("DEPENDENCY", format_dependency_doctor(statuses, verbose=True))
