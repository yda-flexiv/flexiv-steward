"""Command-line interface for flexiv-steward."""

from __future__ import annotations

import argparse
import json
import sys

import argcomplete

from flexiv_steward.branchdiff import _branchdiff_command
from flexiv_steward.build import build_command, configure_command, resolve_profile, run_command, workflow_test_command
from flexiv_steward.context import collect_context
from flexiv_steward.context import discover_project
from flexiv_steward.deps import dependency_doctor, has_blocking_errors, has_dependency_errors
from flexiv_steward.errors import ProjectError
from flexiv_steward.formatters import (
    _gib,
    format_context,
    format_dependency_doctor,
    format_handoff,
    format_sync,
    format_worktree_doctor,
    format_worktree_list,
)
from flexiv_steward.gc import clean_builds
from flexiv_steward.process import run_git
from flexiv_steward.serialization import to_jsonable
from flexiv_steward.sessions import SOURCES, handoff
from flexiv_steward.setup import setup_worktree
from flexiv_steward.sync import sync_worktree
from flexiv_steward.worktree import (
    add_worktree,
    collect_worktree_summaries,
    inspect_worktrees,
    plan_worktree_removal,
    remove_worktree,
    resolve_worktree,
)


def _context_command(args: argparse.Namespace) -> int:
    context = collect_context(args.project)
    if args.json:
        print(json.dumps(to_jsonable(context), indent=2, sort_keys=True))
    else:
        print(format_context(context))
    return 0


def _worktree_doctor_command(args: argparse.Namespace) -> int:
    worktrees = inspect_worktrees(discover_project(args.project))
    if args.json:
        print(json.dumps(to_jsonable(worktrees), indent=2, sort_keys=True))
    else:
        print(format_worktree_doctor(worktrees))
    return 0


def _worktree_list_command(args: argparse.Namespace) -> int:
    summaries = collect_worktree_summaries(discover_project(args.project))
    if args.json:
        print(json.dumps(to_jsonable(summaries), indent=2, sort_keys=True))
    else:
        print(format_worktree_list(summaries))
    return 0


def _worktree_add_command(args: argparse.Namespace) -> int:
    project = discover_project(args.project)
    try:
        result = add_worktree(project, args.branch, args.parent)
    except (ProjectError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 4
    print(result.path)
    if result.note:
        print(result.note, file=sys.stderr)
    return 3 if result.state == "exists" else 0


def _worktree_remove_command(args: argparse.Namespace) -> int:
    project = discover_project(args.project)
    target = plan_worktree_removal(project, args.worktree)
    try:
        answer = input(f"Remove worktree {target} and all of its files? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in {"y", "yes"}:
        print("Aborted; nothing was removed.")
        return 1
    remove_worktree(project, target)
    print(f"Removed worktree {target}.")
    return 0


def _worktree_switch_command(args: argparse.Namespace) -> int:
    print(resolve_worktree(discover_project(args.project), args.worktree))
    return 0


def _build_command(args: argparse.Namespace) -> int:
    project = discover_project(args.project)
    if not args.skip_deps_check:
        statuses = dependency_doctor(project)
        if has_blocking_errors(statuses):
            print(format_dependency_doctor(statuses), file=sys.stderr)
            return 1
        if has_dependency_errors(statuses):
            print(format_dependency_doctor(statuses), file=sys.stderr)
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
        print(json.dumps(to_jsonable(data), indent=2, sort_keys=True))
    else:
        print(format_handoff(data))
    return 0


def _worktree_sync_command(args: argparse.Namespace) -> int:
    result = sync_worktree(discover_project(args.project), args.worktree, args.base, args.dry_run)
    print(format_sync(result))
    return 0 if result.state in {"synced", "dry-run"} else 1


def _dependency_doctor_command(args: argparse.Namespace) -> int:
    statuses = dependency_doctor(discover_project(args.project))
    if args.json:
        print(json.dumps(to_jsonable(statuses), indent=2, sort_keys=True))
    else:
        print(format_dependency_doctor(statuses, args.verbose))
    return 1 if args.strict and has_dependency_errors(statuses) else 0


def _setup_command(args: argparse.Namespace) -> int:
    project = discover_project(args.project)
    profile = resolve_profile(args.profile, args.preset)
    return setup_worktree(project, profile, args.dry_run)


def _gc_builds_command(args: argparse.Namespace) -> int:
    result = clean_builds(discover_project(args.project), args.dry_run)
    if args.json:
        print(json.dumps(to_jsonable(result), indent=2, sort_keys=True))
    else:
        print(f"Keeping current worktree: {result.kept_worktree}")
        action = "Would remove" if result.dry_run else "Removed"
        for path in result.paths:
            print(f"{action}: {path} ({_gib(result.size_bytes[path])})")
        for path in result.skipped:
            print(f"Skipped symlink: {path}")
        print(f"{action} {len(result.paths)} build/install directories ({_gib(result.total_bytes)} total).")
    return 0


def _complete_worktrees(
    prefix: str = "", parsed_args: argparse.Namespace | None = None, **_: object
) -> list[str]:
    try:
        summaries = collect_worktree_summaries(discover_project(getattr(parsed_args, "project", None)))
    except (ProjectError, OSError):
        return []
    choices = {summary.path.name for summary in summaries}
    choices.update(summary.branch for summary in summaries if summary.branch)
    return sorted(choice for choice in choices if choice.startswith(prefix))


def _complete_branches(
    prefix: str = "", parsed_args: argparse.Namespace | None = None, **_: object
) -> list[str]:
    try:
        project = discover_project(getattr(parsed_args, "project", None))
        output = run_git(project, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes")
    except (ProjectError, OSError):
        return []
    choices = {
        branch for branch in output.splitlines() if branch and not branch.endswith("/HEAD")
    }
    return sorted(choice for choice in choices if choice.startswith(prefix))


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
    sync_worktree = sync.add_argument("worktree", help="worktree directory basename, branch name, or absolute path")
    setattr(sync_worktree, "completer", _complete_worktrees)
    sync_base = sync.add_argument("--base", default="origin/v4.x_develop", help="base ref to fetch and merge")
    setattr(sync_base, "completer", _complete_branches)
    sync.add_argument("--dry-run", action="store_true", help="print commands without fetching or merging")
    sync.set_defaults(handler=_worktree_sync_command)
    wt_list = worktree_commands.add_parser("list", help="list worktrees with their branch and parent branch")
    wt_list.add_argument("--json", action="store_true", help="emit JSON for tools and agents")
    wt_list.set_defaults(handler=_worktree_list_command)
    wt_add = worktree_commands.add_parser("add", help="fetch a parent branch and create a worktree for a new branch")
    wt_add.add_argument("branch", help="new branch name, for example task/SW-123-fix-thing")
    wt_add.add_argument(
        "separator", choices=("from",), metavar="from",
        help="literal keyword separating the branch from its parent",
    )
    wt_parent = wt_add.add_argument("parent", help="base branch, for example origin/release/v4.2")
    setattr(wt_parent, "completer", _complete_branches)
    wt_add.set_defaults(handler=_worktree_add_command)
    wt_remove = worktree_commands.add_parser("rm", help="delete a worktree after confirmation")
    wt_remove_worktree = wt_remove.add_argument("worktree", help="worktree directory basename, branch name, or absolute path")
    setattr(wt_remove_worktree, "completer", _complete_worktrees)
    wt_remove.set_defaults(handler=_worktree_remove_command)
    wt_switch = worktree_commands.add_parser("switch", help="print another worktree's path for cd")
    wt_switch_worktree = wt_switch.add_argument("worktree", help="worktree directory basename, branch name, or absolute path")
    setattr(wt_switch_worktree, "completer", _complete_worktrees)
    wt_switch.set_defaults(handler=_worktree_switch_command)
    dependencies = subcommands.add_parser("deps", help="inspect external-repository and submodule revisions")
    dependency_commands = dependencies.add_subparsers(dest="dependency_command", required=True)
    dependency_doctor_parser = dependency_commands.add_parser("doctor", help="compare checked-out dependencies with declared revisions")
    dependency_doctor_parser.add_argument("--strict", action="store_true", help="exit nonzero for missing or mismatched revisions")
    dependency_doctor_parser.add_argument("--verbose", action="store_true", help="show every checked dependency revision")
    dependency_doctor_parser.add_argument("--json", action="store_true", help="emit JSON for tools and agents")
    dependency_doctor_parser.set_defaults(handler=_dependency_doctor_command)
    build = subcommands.add_parser("build", help="configure and build a Docker/CMake profile")
    build.add_argument("profile", nargs="?", default="all", help="all, rca, ui, debug, or yocto-all")
    build.add_argument("--preset", help="use a CMake preset instead of a named profile")
    build.add_argument("--configure", action="store_true", help="configure the preset before building")
    build.add_argument("--target", action="append", default=[], help="CMake target to build; may be repeated")
    build.add_argument("--skip-deps-check", action="store_true", help="do not block the build on dependency revision drift")
    build.add_argument("--dry-run", action="store_true", help="print commands without executing them")
    build.set_defaults(handler=_build_command)
    setup = subcommands.add_parser("setup", help="bootstrap a new worktree and verify dependency revisions")
    setup.add_argument("profile", nargs="?", default="rca", help="all, rca, ui, debug, or yocto-all")
    setup.add_argument("--preset", help="use a CMake preset instead of a named profile")
    setup.add_argument("--dry-run", action="store_true", help="print setup commands without executing them")
    setup.set_defaults(handler=_setup_command)
    gc = subcommands.add_parser("gc", help="clean worktree build artifacts")
    gc_commands = gc.add_subparsers(dest="gc_command", required=True)
    gc_builds = gc_commands.add_parser(
        "builds", help="remove build/install artifacts from every other worktree"
    )
    gc_builds.add_argument(
        "--dry-run", action="store_true",
        help="preview directories without deleting anything",
    )
    gc_builds.add_argument(
        "--json", action="store_true",
        help="emit JSON for tools and agents",
    )
    gc_builds.set_defaults(handler=_gc_builds_command)
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
    branchdiff = subcommands.add_parser("branchdiff", help="serve a web UI comparing two branches")
    branchdiff.add_argument("branch_a", help="base branch or ref")
    branchdiff.add_argument("branch_b", nargs="?", default="HEAD", help="target branch or ref (default: HEAD)")
    branchdiff.add_argument("--port", type=int, default=9876, help="HTTP server port (default: 9876)")
    branchdiff.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    branchdiff.set_defaults(handler=_branchdiff_command)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    argcomplete.autocomplete(parser)
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
