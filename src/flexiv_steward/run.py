"""Launch locally built applications with their matching thirdparty runtime."""

from __future__ import annotations

import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from flexiv_steward.errors import ProjectError
from flexiv_steward.layout import source_root
from flexiv_steward.presets import compile_commands_path, configure_preset
from flexiv_steward.process import merged_environment, run_process


APPLICATIONS = {"rca": "RobotControlApp", "uiapp": "UIApp"}

# RCA serves Modbus TCP on the privileged port 502. authbind grants that bind
# to a non-root user; --deep keeps its LD_PRELOAD shim across the launch script
# and every process the script starts. Authorization comes from the host's
# /etc/authbind/byport/502, so only that port is opened up.
AUTHBIND_PORT = 502
AUTHBIND_PREFIX = ("authbind", "--deep")


@dataclass(frozen=True)
class RunPlan:
    command: list[str]
    cwd: Path
    environment: dict[str, str]
    thirdparty: Path


def _cache_values(build_dir: Path) -> dict[str, str]:
    cache = build_dir / "CMakeCache.txt"
    if not cache.is_file():
        return {}
    values = {}
    for line in cache.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and ":" in key and not key.startswith(("//", "#")):
            values[key.split(":", 1)[0]] = value
    return values


def _thirdparty_root(project: Path, cache: dict[str, str]) -> Path:
    # A versioned host path in the build cache identifies the libraries actually
    # used by this build. Never silently replace a missing one with another ABI.
    cached = cache.get("EXTERNAL_ThirdParty2_DIR", "")
    canonical = "/opt/flexiv_thirdparty2"
    if cached and cached != canonical:
        root = Path(cached)
        if not root.is_absolute():
            raise ProjectError(f"thirdparty path in CMakeCache.txt is not absolute: {cached}")
        return root

    config = project / "docker" / "docker_config.sh"
    if config.is_file():
        # Use the checkout's own rules, including local.env and older image
        # naming schemes. Sourcing configuration does not start Docker or call
        # ensure_thirdparty2_host_access (which may create volumes/use sudo).
        result = run_process([
            "bash", "-c",
            'source "$1" >/dev/null || exit $?; '
            'printf "%s" "${FLEXIV_THIRDPARTY2_SHARED_PATH:-${flexiv_thirdparty2_path:-}}"',
            "fs-run", str(config),
        ], project, check=True)
        if result.stdout.strip() and result.stdout.strip() != canonical:
            return Path(result.stdout.strip())
    if cached and Path(cached).is_dir():
        return Path(cached)
    raise ProjectError(
        "cannot determine host thirdparty libraries from CMakeCache.txt or "
        "docker/docker_config.sh; run `fs setup <preset>` first"
    )


def plan_run(
    project: Path, application: str, arguments: list[str], *,
    preset: str | None = None, build_dir: str | None = None, serial: str | None = None,
    authbind: bool = True,
) -> RunPlan:
    """Resolve the executable, working directory and runtime without launching."""
    if preset and build_dir:
        raise ProjectError("choose either --preset or --build-dir")
    if preset:
        selected = configure_preset(project, preset)
        if selected.platform != "ubuntu":
            raise ProjectError("fs run supports host Linux builds; choose an Ubuntu preset")
        directory = compile_commands_path(project, preset).parent
    elif build_dir:
        directory = Path(build_dir).expanduser()
        if not directory.is_absolute():
            directory = source_root(project) / directory
    else:
        directory = source_root(project) / "build" / "Linux" / "RelWithDebInfo"
    if not directory.is_absolute():
        directory = source_root(project) / directory
    directory = directory.resolve()
    name = APPLICATIONS.get(application.lower())
    if name is None:
        raise ProjectError(f"unknown application: {application}")
    app_args = list(arguments)
    if serial is not None:
        if name != "RobotControlApp":
            raise ProjectError("a robot serial number is supported only for `fs run rca`")
        if "-s" in app_args or "--arm_sn" in app_args:
            raise ProjectError("supply the robot serial number either positionally or with -s, not both")
        model, separator, suffix = serial.partition("-")
        if not separator or not suffix or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", model):
            raise ProjectError(f"invalid robot serial number '{serial}'; expected e.g. AX01H-06-D1-Yangda")
        inferred = ["-s", serial]
        if "-c" not in app_args and "--cfg_file" not in app_args:
            config = directory / "specs" / "robots" / f"Flexiv{model.upper()}" / "flexivCfg.xml"
            if not config.is_file():
                raise ProjectError(
                    f"robot config inferred from '{serial}' is missing: {config}; "
                    "supply an explicit config with `-- -c <path>`"
                )
            inferred.extend(["-c", str(config)])
        app_args[:0] = inferred
    relative_paths = [Path("app") / name / name]
    if name == "UIApp":
        relative_paths.append(Path("external/flexiv_sw_uiapp/app/UIApp/UIApp"))
    binaries = [directory / path for path in relative_paths if (directory / path).is_file()]
    if len(binaries) != 1:
        raise ProjectError(
            f"expected one {name} executable in {directory}, found {len(binaries)}; "
            "build it with `fs build <preset> --target " + name + "` or select --preset/--build-dir"
        )
    binary = binaries[0]
    if not os.access(binary, os.X_OK):
        raise ProjectError(f"application is not executable: {binary}")

    cache = _cache_values(directory)
    thirdparty = _thirdparty_root(project, cache)
    runtime = thirdparty / "install" / "Linux" / cache.get("EXTERNAL_ThirdParty_BUILD_TYPE", "Release")
    if not runtime.is_dir():
        raise ProjectError(
            f"matching thirdparty runtime is missing or unreadable: {runtime}; "
            "initialize this checkout's Docker build environment with `fs setup <preset>`"
        )
    qt_roots = sorted(path for path in (runtime / "tools" / "qtdep").glob("*/gcc_64")
                      if (path / "lib").is_dir() and (path / "plugins").is_dir())
    if len(qt_roots) != 1:
        raise ProjectError(f"expected one Qt runtime in {runtime / 'tools/qtdep'}, found {len(qt_roots)}")
    qt = qt_roots[0]
    libraries = [qt / "lib", runtime / "include" / "mujoco" / "lib", runtime / "lib"]
    paths = [str(path) for path in libraries if path.is_dir()]
    paths.extend(path for path in os.environ.get("LD_LIBRARY_PATH", "").split(":") if path)
    environment = {
        "LD_LIBRARY_PATH": ":".join(dict.fromkeys(paths)),
        "QT_QPA_PLATFORM_PLUGIN_PATH": str(qt / "plugins"),
        "flexiv_thirdparty2_path": str(thirdparty),
        # RCA's script uses this for specs/ and user_data/. Both come from
        # the selected build, rather than the wrapper repository root.
        "flexiv_sw_path": str(directory),
        "flexiv_sw_build_path": str(directory),
    }
    script_name = "run_rca_for_ubuntu.sh" if name == "RobotControlApp" else "run_ui_for_ubuntu.sh"
    script = project / script_name
    if not script.is_file():
        script = source_root(project) / script_name
    if script.is_file():
        command = ["bash", str(script), *app_args]
    else:
        if name == "UIApp" and not any(arg == "-p" or arg.startswith("-p") for arg in app_args):
            app_args[:0] = ["-p", "ubuntu_pc"]
        command = [str(binary), *app_args]
    if name == "RobotControlApp" and authbind:
        command = [*AUTHBIND_PREFIX, *command]
    return RunPlan(command, directory, environment, thirdparty)


def run_application(plan: RunPlan, dry_run: bool = False) -> int:
    """Print a reproducible launch, then preserve the application's exit code."""
    if not dry_run and plan.command[:1] == ["authbind"] and shutil.which("authbind") is None:
        raise ProjectError(
            f"authbind is required for RCA to bind port {AUTHBIND_PORT} without root; "
            "install it (`sudo apt install authbind`) and authorize the port "
            f"(`sudo touch /etc/authbind/byport/{AUTHBIND_PORT} && "
            f"sudo chown \"$USER\" /etc/authbind/byport/{AUTHBIND_PORT} && "
            f"sudo chmod 755 /etc/authbind/byport/{AUTHBIND_PORT}`), "
            "or pass --no-authbind"
        )
    print(f"$ cd {shlex.quote(str(plan.cwd))} && " + shlex.join([
        "env", *(f"{key}={value}" for key, value in plan.environment.items()), *plan.command,
    ]), flush=True)
    if dry_run:
        return 0
    try:
        return run_process(plan.command, plan.cwd, capture_output=False, timeout=None,
                           env=merged_environment(**plan.environment)).returncode
    except KeyboardInterrupt:
        return 130
