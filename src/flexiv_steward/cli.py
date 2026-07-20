"""Command-line interface for flexiv-steward."""

from __future__ import annotations

import argparse
import json
import sys

from flexiv_steward.build import build_command, configure_command, resolve_profile, run_command, workflow_test_command
from flexiv_steward.builds import build_json, cmake_gui_command, format_builds, list_builds, open_cmake_gui, select_build
from flexiv_steward.context import ProjectError, collect_context, format_context
from flexiv_steward.context import discover_project
from flexiv_steward.sessions import SOURCES, format_handoff, handoff
from flexiv_steward.sync import format_sync, sync_worktree
from flexiv_steward.worktree import format_worktree_doctor, inspect_worktrees


def _context_command(args: argparse.Namespace) -> int:
    context = collect_context(args.project)
    if args.json:
        print(json.dumps(context, indent=2, sort_keys=True))
    else:
        print(format_context(context))
    return 0


def _worktree_doctor_command(args: argparse.Namespace) -> int:
    worktrees = inspect_worktrees(discover_project(args.project))
    if args.json:
        print(json.dumps(worktrees, indent=2, sort_keys=True))
    else:
        print(format_worktree_doctor(worktrees))
    return 0


def _build_command(args: argparse.Namespace) -> int:
    project = discover_project(args.project)
    profile = resolve_profile(args.profile, args.preset)
    if args.configure:
        result = run_command(configure_command(project, profile), project, args.dry_run)
        if result:
            return result
    return run_command(build_command(project, profile, args.target), project, args.dry_run)


def _test_workflow_command(args: argparse.Namespace) -> int:
    project = discover_project(args.project)
    return run_command(workflow_test_command(project), project, args.dry_run)


def _session_handoff_command(args: argparse.Namespace) -> int:
    project = discover_project(args.project)
    data = handoff(project, args.days, args.source or SOURCES, args.limit)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(format_handoff(data))
    return 0


def _worktree_sync_command(args: argparse.Namespace) -> int:
    result = sync_worktree(discover_project(args.project), args.worktree, args.base, args.dry_run)
    print(format_sync(result))
    return 0 if result.state in {"synced", "dry-run"} else 1


def _build_list_command(args: argparse.Namespace) -> int:
    builds = list_builds(discover_project(args.project))
    if args.json:
        print(json.dumps(build_json(builds), indent=2, sort_keys=True))
    else:
        print(format_builds(builds))
    return 0


def _build_gui_command(args: argparse.Namespace) -> int:
    project = discover_project(args.project)
    build = select_build(list_builds(project), args.directory)
    return open_cmake_gui(project, build, args.dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fs", description="Flexiv development-workspace helper")
    parser.add_argument("--project", help="Git worktree to operate on (default: discover from cwd)")
    subcommands = parser.add_subparsers(dest="command", required=True)
    context = subcommands.add_parser("context", help="show the current project snapshot")
    context.add_argument("--json", action="store_true", help="emit JSON for tools and agents")
    context.set_defaults(handler=_context_command)
    worktree = subcommands.add_parser("wt", help="manage and inspect Git worktrees")
    worktree_commands = worktree.add_subparsers(dest="worktree_command", required=True)
    doctor = worktree_commands.add_parser("doctor", help="report dirty, locked, and prunable worktrees")
    doctor.add_argument("--json", action="store_true", help="emit JSON for tools and agents")
    doctor.set_defaults(handler=_worktree_doctor_command)
    sync = worktree_commands.add_parser("sync", help="fetch and merge a base branch into one clean worktree")
    sync.add_argument("worktree", help="worktree directory name or absolute path")
    sync.add_argument("--base", default="origin/v4.x_develop", help="base ref to fetch and merge")
    sync.add_argument("--dry-run", action="store_true", help="print commands without fetching or merging")
    sync.set_defaults(handler=_worktree_sync_command)
    builds = subcommands.add_parser("builds", help="list configured CMake build directories")
    builds.add_argument("--json", action="store_true", help="emit JSON for tools and agents")
    builds.set_defaults(handler=_build_list_command)
    build_commands = builds.add_subparsers(dest="builds_command")
    gui = build_commands.add_parser("gui", help="select a build directory and open it in cmake-gui")
    gui.add_argument("directory", nargs="?", help="relative build directory or unique basename")
    gui.add_argument("--dry-run", action="store_true", help="print the cmake-gui command without opening it")
    gui.set_defaults(handler=_build_gui_command)
    build = subcommands.add_parser("build", help="configure and build a Docker/CMake profile")
    build.add_argument("profile", nargs="?", default="all", help="all, rca, ui, debug, or yocto-all")
    build.add_argument("--preset", help="use a CMake preset instead of a named profile")
    build.add_argument("--configure", action="store_true", help="configure the preset before building")
    build.add_argument("--target", action="append", default=[], help="CMake target to build; may be repeated")
    build.add_argument("--dry-run", action="store_true", help="print commands without executing them")
    build.set_defaults(handler=_build_command)
    test = subcommands.add_parser("test", help="run project test workflows")
    test_commands = test.add_subparsers(dest="test_command", required=True)
    workflow = test_commands.add_parser("workflow", help="run the WorkflowTest integration runner")
    workflow.add_argument("--dry-run", action="store_true", help="print the command without executing it")
    workflow.set_defaults(handler=_test_workflow_command)
    session = subcommands.add_parser("session", help="summarize local agent work for the current worktree")
    session_commands = session.add_subparsers(dest="session_command", required=True)
    handoff_parser = session_commands.add_parser("handoff", help="create a redacted continuation brief")
    handoff_parser.add_argument("--days", type=int, default=7, help="look back this many days (default: 7)")
    handoff_parser.add_argument("--limit", type=int, default=8, help="maximum sessions to include (default: 8)")
    handoff_parser.add_argument("--source", action="append", choices=SOURCES, help="include only this source; may repeat")
    handoff_parser.add_argument("--json", action="store_true", help="emit JSON for tools and agents")
    handoff_parser.set_defaults(handler=_session_handoff_command)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        exit_code = args.handler(args)
    except ProjectError as error:
        parser.error(str(error))
    except OSError as error:
        parser.error(str(error))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
