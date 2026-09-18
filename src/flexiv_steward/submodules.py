"""Space-efficient, cache-backed Git submodule initialization."""

from __future__ import annotations

import shlex
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flexiv_steward.gitcache import cache_path, ensure_cache
from flexiv_steward.models import SubmoduleSpec
from flexiv_steward.process import run_git, run_process


def submodule_command() -> list[str]:
    return ["git", "submodule", "update", "--init", "--recursive"]


def cache_root(project: Path) -> Path:
    common = run_git(project, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common) / "flexiv-steward" / "submodules-v2"


def _specs(parent: Path) -> tuple[SubmoduleSpec, ...]:
    modules_file = parent / ".gitmodules"
    if not modules_file.is_file():
        return ()
    output = run_process(
        ["git", "config", "--file=.gitmodules", "--get-regexp", r"^submodule\..*\.path$"],
        parent,
    )
    if output.returncode not in {0, 1}:
        return ()
    specs: list[SubmoduleSpec] = []
    for line in output.stdout.splitlines():
        key, path_text = line.split(None, 1)
        name = key.removeprefix("submodule.").removesuffix(".path")
        url = run_git(parent, "config", "--file=.gitmodules", "--get", f"submodule.{name}.url")
        tree = run_git(parent, "ls-tree", "HEAD", "--", path_text).split()
        if len(tree) >= 3 and tree[1] == "commit":
            specs.append(SubmoduleSpec(name, Path(path_text), url, tree[2]))
    return tuple(specs)


def _checkout_from_cache(parent: Path, spec: SubmoduleSpec, cache: Path) -> None:
    command = [
        "git",
        "-c",
        "protocol.file.allow=always",
        "-c",
        f"submodule.{spec.name}.url={cache}",
        "submodule",
        "update",
        "--init",
        "--no-fetch",
        "--",
        str(spec.path),
    ]
    run_process(command, parent, timeout=300, capture_output=False, check=True)
    checkout = parent / spec.path
    run_process(["git", "remote", "set-url", "origin", spec.url], checkout, check=True)


def _initialize_level(parent: Path, root: Path, dry_run: bool) -> tuple[Path, ...]:
    specs = _specs(parent)
    if not specs:
        return ()
    caches = tuple(cache_path(root, spec.url) for spec in specs)
    if dry_run:
        for spec, cache in zip(specs, caches):
            print(f"$ cache {shlex.quote(spec.url)} at {shlex.quote(str(cache))} commit {spec.commit}")
            print(
                "$ "
                + shlex.join(
                    [
                        "git", "-c", "protocol.file.allow=always",
                        "-c", f"submodule.{spec.name}.url={cache}",
                        "submodule", "update", "--init", "--no-fetch", "--", str(spec.path),
                    ]
                )
            )
        return tuple(parent / spec.path for spec in specs)
    unique: dict[Path, tuple[str, list[str]]] = {}
    for spec, cache in zip(specs, caches):
        url, commits = unique.setdefault(cache, (spec.url, []))
        if spec.commit not in commits:
            commits.append(spec.commit)
    with ThreadPoolExecutor(max_workers=min(8, len(unique))) as executor:
        futures = [
            executor.submit(ensure_cache, cache, url, tuple(commits))
            for cache, (url, commits) in unique.items()
        ]
        for future in futures:
            future.result()
    for spec, cache in zip(specs, caches):
        _checkout_from_cache(parent, spec, cache)
    return tuple(parent / spec.path for spec in specs)


def initialize_tree(parent: Path, root: Path, dry_run: bool) -> None:
    """Initialize every submodule level below *parent* from caches under *root*."""
    pending = [parent]
    while pending:
        node = pending.pop(0)
        pending.extend(_initialize_level(node, root, dry_run))


def initialize_submodules(project: Path, dry_run: bool) -> int:
    """Initialize nested submodules from exact-commit shallow caches."""
    if not (project / ".gitmodules").is_file():
        if dry_run:
            print("$ " + shlex.join(submodule_command()))
            return 0
        return run_process(submodule_command(), project, timeout=600, capture_output=False).returncode
    initialize_tree(project, cache_root(project), dry_run)
    return 0
