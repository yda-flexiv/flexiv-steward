"""Command-line interface for flexiv-steward."""

from __future__ import annotations

import argparse
import json
import sys

from flexiv_steward.context import ProjectError, collect_context, format_context


def _context_command(args: argparse.Namespace) -> int:
    context = collect_context(args.project)
    if args.json:
        print(json.dumps(context, indent=2, sort_keys=True))
    else:
        print(format_context(context))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fs", description="Flexiv development-workspace helper")
    parser.add_argument("--project", help="Git worktree to operate on (default: discover from cwd)")
    subcommands = parser.add_subparsers(dest="command", required=True)
    context = subcommands.add_parser("context", help="show the current project snapshot")
    context.add_argument("--json", action="store_true", help="emit JSON for tools and agents")
    context.set_defaults(handler=_context_command)
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
