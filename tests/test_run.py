import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from flexiv_steward.cli import main
from flexiv_steward.errors import ProjectError
from flexiv_steward.run import plan_run, run_application


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fs run ")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        self.source = self.project / "flexiv"
        self.build = self.source / "build/Linux/RelWithDebInfo"
        self.build.mkdir(parents=True)
        self.thirdparty = self.project / "thirdparty pinned"
        self.runtime = self.thirdparty / "install/Linux/Release"
        self.qt = self.runtime / "tools/qtdep/5.12.3/gcc_64"
        for path in (self.qt / "lib", self.qt / "plugins", self.runtime / "lib",
                     self.runtime / "include/mujoco/lib"):
            path.mkdir(parents=True)
        (self.build / "CMakeCache.txt").write_text(
            f"EXTERNAL_ThirdParty2_DIR:PATH={self.thirdparty}\n")
        self.rca = self.binary("app/RobotControlApp/RobotControlApp")
        self.ui = self.binary("external/flexiv_sw_uiapp/app/UIApp/UIApp")
        self.authbind = ["authbind", "--deep"]

    def binary(self, relative):
        binary = self.build / relative
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        return binary

    def test_pinned_runtime_precedes_config_and_preserves_environment(self):
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": ":/custom/lib:", "DISPLAY": ":9"}):
            plan = plan_run(self.project, "rca", ["-c", "specs/a b.xml", "; touch /no"])
        self.assertEqual(plan.command, [*self.authbind, str(self.rca), "-c", "specs/a b.xml", "; touch /no"])
        self.assertEqual(plan.cwd, self.build)
        self.assertEqual(plan.thirdparty, self.thirdparty)
        self.assertEqual(plan.environment["LD_LIBRARY_PATH"].split(":"), [
            str(self.qt / "lib"), str(self.runtime / "include/mujoco/lib"),
            str(self.runtime / "lib"), "/custom/lib"])
        self.assertEqual(plan.environment["QT_QPA_PLATFORM_PLUGIN_PATH"], str(self.qt / "plugins"))

    def test_docker_canonical_path_maps_to_checkouts_config(self):
        (self.build / "CMakeCache.txt").write_text("EXTERNAL_ThirdParty2_DIR:PATH=/opt/flexiv_thirdparty2\n")
        docker = self.project / "docker"
        docker.mkdir()
        (docker / "docker_config.sh").write_text(
            'source "$(dirname "${BASH_SOURCE[0]}")/local.env"\n'
            'echo "config chatter"\n'
            'ensure_thirdparty2_host_access() { touch should-not-exist; }\n')
        (docker / "local.env").write_text(f"FLEXIV_THIRDPARTY2_SHARED_PATH='{self.thirdparty}'\n")
        self.assertEqual(plan_run(self.project, "rca", []).thirdparty, self.thirdparty)
        self.assertFalse((self.project / "should-not-exist").exists())

    def test_missing_pinned_libraries_never_fall_back_to_another_version(self):
        (self.build / "CMakeCache.txt").write_text("EXTERNAL_ThirdParty2_DIR:PATH=/missing/pinned\n")
        with self.assertRaisesRegex(ProjectError, "matching thirdparty runtime"):
            plan_run(self.project, "rca", [])

    def test_missing_or_nonexecutable_binary_fails_before_runtime_resolution(self):
        self.rca.unlink()
        with self.assertRaisesRegex(ProjectError, "fs build"):
            plan_run(self.project, "rca", [])
        self.binary("app/RobotControlApp/RobotControlApp").chmod(0o644)
        with self.assertRaisesRegex(ProjectError, "not executable"):
            plan_run(self.project, "rca", [])

    def test_ui_platform_default_can_be_overridden(self):
        self.assertEqual(plan_run(self.project, "UIApp", []).command, [str(self.ui), "-p", "ubuntu_pc"])
        self.assertEqual(plan_run(self.project, "uiapp", ["-p", "surface"]).command,
                         [str(self.ui), "-p", "surface"])

    def test_rca_uses_launch_script_and_supplies_its_build_environment(self):
        script = self.project / "run_rca_for_ubuntu.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        arguments = ["-s", "AX01H-06-D1-YangDa", "-c", "specs/robots/FlexivAX01H/flexivCfg.xml"]
        plan = plan_run(self.project, "rca", arguments)
        self.assertEqual(plan.command, [*self.authbind, "bash", str(script), *arguments])
        self.assertEqual(plan.environment["flexiv_sw_path"], str(self.build))
        self.assertEqual(plan.environment["flexiv_sw_build_path"], str(self.build))

    def test_authbind_wraps_both_rca_launch_paths_and_can_be_disabled(self):
        script = self.project / "run_rca_for_ubuntu.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        self.assertEqual(plan_run(self.project, "rca", []).command,
                         [*self.authbind, "bash", str(script)])
        self.assertEqual(plan_run(self.project, "rca", [], authbind=False).command,
                         ["bash", str(script)])
        script.unlink()
        self.assertEqual(plan_run(self.project, "rca", []).command, [*self.authbind, str(self.rca)])
        self.assertEqual(plan_run(self.project, "rca", [], authbind=False).command, [str(self.rca)])
        self.assertEqual(plan_run(self.project, "uiapp", []).command, [str(self.ui), "-p", "ubuntu_pc"])

    def test_run_application_executes_authbind_and_explains_missing_authbind(self):
        plan = plan_run(self.project, "rca", [])
        with patch("flexiv_steward.run.run_process") as run, redirect_stdout(io.StringIO()):
            run.return_value.returncode = 0
            self.assertEqual(run_application(plan), 0)
        self.assertEqual(run.call_args.args[0], [*self.authbind, str(self.rca)])
        with patch("flexiv_steward.run.shutil.which", return_value=None), redirect_stdout(io.StringIO()):
            self.assertEqual(run_application(plan, dry_run=True), 0)
            with self.assertRaisesRegex(ProjectError, "authbind is required"):
                run_application(plan)

    def test_rca_serial_infers_config_and_preserves_serial_case(self):
        config = self.build / "specs/robots/FlexivAX01H/flexivCfg.xml"
        config.parent.mkdir(parents=True)
        config.touch()
        script = self.project / "run_rca_for_ubuntu.sh"
        script.touch()
        plan = plan_run(self.project, "rca", [], serial="AX01H-06-D1-Yangda")
        self.assertEqual(plan.command,
                         [*self.authbind, "bash", str(script), "-s", "AX01H-06-D1-Yangda", "-c", str(config)])
        plan = plan_run(self.project, "rca", [], serial="ax01h-06-D1-Yangda")
        self.assertEqual(plan.command[-3:], ["ax01h-06-D1-Yangda", "-c", str(config)])

    def test_rca_serial_allows_explicit_config_without_inferred_file(self):
        plan = plan_run(self.project, "rca", ["-c", "custom config.xml"], serial="AX01H-06-D1-Yangda")
        self.assertEqual(plan.command[3:], ["-s", "AX01H-06-D1-Yangda", "-c", "custom config.xml"])

    def test_rca_serial_rejects_missing_config_invalid_serial_and_conflicting_options(self):
        with self.assertRaisesRegex(ProjectError, "supply an explicit config"):
            plan_run(self.project, "rca", [], serial="UNKNOWN-06-D1-Test")
        for serial in ("AX01H", "../AX01H-06-D1-Test", "AX01H-"):
            with self.subTest(serial=serial), self.assertRaisesRegex(ProjectError, "invalid robot serial"):
                plan_run(self.project, "rca", [], serial=serial)
        with self.assertRaisesRegex(ProjectError, "not both"):
            plan_run(self.project, "rca", ["-s", "other"], serial="AX01H-06-D1-Yangda")
        with self.assertRaisesRegex(ProjectError, "only for"):
            plan_run(self.project, "uiapp", [], serial="AX01H-06-D1-Yangda")

    def test_cli_short_serial_form_with_build_options_and_script_arguments(self):
        config = self.build / "specs/robots/FlexivAX01H/flexivCfg.xml"
        config.parent.mkdir(parents=True)
        config.touch()
        for extra in ([], ["--", "-G"], ["--", "-c", "custom config.xml"]):
            with self.subTest(extra=extra), redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()):
                with patch("flexiv_steward.run.run_process") as run, self.assertRaises(SystemExit) as result:
                    main(["--project", str(self.project), "run", "rca", "AX01H-06-D1-Yangda", "--dry-run", *extra])
            self.assertEqual(result.exception.code, 0)
            self.assertIn("-s AX01H-06-D1-Yangda -c", stdout.getvalue())
            if extra and extra[-1] == "-G":
                self.assertIn("-G", stdout.getvalue())
            if "custom config.xml" in extra:
                self.assertIn("'custom config.xml'", stdout.getvalue())
                self.assertNotIn(str(config), stdout.getvalue())
            run.assert_not_called()

    def test_cli_dry_run_shows_authbind_and_no_authbind_bypasses_it(self):
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()), \
                patch("flexiv_steward.run.run_process") as run:
            with self.assertRaises(SystemExit) as result:
                main(["--project", str(self.project), "run", "rca", "--dry-run"])
        self.assertEqual(result.exception.code, 0)
        self.assertIn("authbind --deep", stdout.getvalue())
        run.assert_not_called()

        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()), \
                patch("flexiv_steward.run.run_process") as run:
            with self.assertRaises(SystemExit) as result:
                main(["--project", str(self.project), "run", "rca", "--no-authbind",
                      "--dry-run", "--", "-G"])
        self.assertEqual(result.exception.code, 0)
        self.assertNotIn("authbind", stdout.getvalue())
        self.assertIn("-G", stdout.getvalue())
        run.assert_not_called()

    def test_ui_uses_launch_script_without_injecting_binary_only_arguments(self):
        script = self.project / "run_ui_for_ubuntu.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        self.assertEqual(plan_run(self.project, "uiapp", []).command, ["bash", str(script)])
        self.assertEqual(plan_run(self.project, "uiapp", ["-G"]).command, ["bash", str(script), "-G"])

    def test_direct_layout_and_explicit_build_directory(self):
        self.assertEqual(plan_run(self.source, "rca", [], build_dir=str(self.build)).command,
                         [*self.authbind, str(self.rca)])
        self.assertEqual(plan_run(self.project, "rca", [], build_dir="build/Linux/RelWithDebInfo").cwd, self.build)

    def test_preset_selects_custom_build_and_rejects_yocto(self):
        (self.source / "CMakePresets.json").write_text(json.dumps({"configurePresets": [
            {"name": "ubuntu-custom", "binaryDir": "${sourceDir}/build/Linux/RelWithDebInfo"},
            {"name": "yocto-custom", "binaryDir": "${sourceDir}/build/Yocto/Release"},
        ]}))
        self.assertEqual(plan_run(self.project, "rca", [], preset="ubuntu-custom").cwd, self.build)
        with self.assertRaisesRegex(ProjectError, "host Linux"):
            plan_run(self.project, "rca", [], preset="yocto-custom")

    def test_cli_dry_run_passes_arguments_without_launching(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), patch("flexiv_steward.run.run_process") as run:
            with self.assertRaises(SystemExit) as result:
                main(["--project", str(self.project), "run", "RCA", "--dry-run", "--", "-c", "a b.xml", "-e"])
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("'a b.xml'", stdout.getvalue())
        run.assert_not_called()

    def test_cli_app_options_do_not_change_fs_options(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), patch("flexiv_steward.run.run_process") as run:
            with self.assertRaises(SystemExit) as result:
                main(["--project", str(self.project), "run", "rca", "--dry-run", "--", "--preset", "app-option", "--dry-run"])
        self.assertEqual(result.exception.code, 0)
        self.assertIn("--preset app-option --dry-run", stdout.getvalue())
        run.assert_not_called()

    def test_cli_rejects_unknown_fs_options(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
            main(["--project", str(self.project), "run", "rca", "--bad-option", "--", "-e"])
        self.assertEqual(result.exception.code, 2)

    def test_relative_preset_build_directory_is_relative_to_source(self):
        (self.source / "CMakePresets.json").write_text(json.dumps({"configurePresets": [
            {"name": "relative", "binaryDir": "build/Linux/RelWithDebInfo"},
        ]}))
        self.assertEqual(plan_run(self.project, "rca", [], preset="relative").cwd, self.build)

    def test_actual_process_gets_cwd_args_environment_and_exit_status(self):
        output = self.project / "observed.json"
        self.rca.write_text(
            "#!/usr/bin/python3\nimport os, sys, json\n"
            f"with open({str(output)!r}, 'w') as f:\n"
            " json.dump([os.getcwd(), sys.argv[1:], os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'], os.environ['DISPLAY']], f)\n"
            "sys.exit(7)\n")
        with patch.dict(os.environ, {"DISPLAY": ":42"}), redirect_stdout(io.StringIO()):
            plan = plan_run(self.project, "rca", ["arg with spaces", "$(touch nope)"], authbind=False)
            status = run_application(plan)
        self.assertEqual(status, 7)
        self.assertEqual(json.loads(output.read_text()), [str(self.build), ["arg with spaces", "$(touch nope)"],
                                                        str(self.qt / "plugins"), ":42"])
        self.assertFalse((self.build / "nope").exists())

    def test_ambiguous_qt_and_ui_binaries_fail(self):
        other_qt = self.runtime / "tools/qtdep/6.0/gcc_64"
        (other_qt / "lib").mkdir(parents=True)
        (other_qt / "plugins").mkdir()
        with self.assertRaisesRegex(ProjectError, "expected one Qt runtime"):
            plan_run(self.project, "rca", [])
        self.binary("app/UIApp/UIApp")
        with self.assertRaisesRegex(ProjectError, "expected one UIApp executable"):
            plan_run(self.project, "uiapp", [])
