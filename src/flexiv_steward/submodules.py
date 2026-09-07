"""Space-efficient, cache-backed Git submodule initialization."""

from __future__ import annotations

import hashlib
import shlex
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flexiv_steward.models import SubmoduleSpec
from flexiv_steward.process import run_git, run_process


def submodule_command() -> list[str]:
    return ["git", "submodule", "update", "--init", "--recursive"]


def _cache_root(project: Path) -> Path:
    common = run_git(project, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common) / "flexiv-steward" / "submodules-v2"


def _cache_path(root: Path, spec: SubmoduleSpec) -> Path:
    digest = hashlib.sha256(spec.url.encode()).hexdigest()[:20]
    return root / f"{digest}.git"


def _is_git_cache(path: Path) -> bool:
    return (path / "HEAD").is_file() and (path / "objects").is_dir()


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


def _has_commit(cache: Path, commit: str) -> bool:
    if not _is_git_cache(cache):
        return False
    result = run_process(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cache)
    return result.returncode == 0


def _ensure_cache(cache: Path, url: str, commits: tuple[str, ...]) -> None:
    missing = tuple(commit for commit in commits if not _has_commit(cache, commit))
    if not missing:
        return
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not _is_git_cache(cache):
        run_process(["git", "init", "--bare", str(cache)], cache.parent, check=True)
        run_process(["git", "remote", "add", "origin", url], cache, check=True)
    else:
        run_process(["git", "remote", "set-url", "origin", url], cache, check=True)
    for commit in missing:
        # A local clone copies heads by default, so expose the exact gitlink
        # commit under a private cache branch.
        reference = f"refs/heads/flexiv-steward-{commit}"
        run_process(
            ["git", "fetch", "--depth=1", "--no-tags", "origin", f"+{commit}:{reference}"],
            cache,
            timeout=600,
            check=True,
        )


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
    caches = tuple(_cache_path(root, spec) for spec in specs)
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
            executor.submit(_ensure_cache, cache, url, tuple(commits))
            for cache, (url, commits) in unique.items()
        ]
        for future in futures:
            future.result()
    for spec, cache in zip(specs, caches):
        _checkout_from_cache(parent, spec, cache)
    return tuple(parent / spec.path for spec in specs)


def initialize_submodules(project: Path, dry_run: bool) -> int:
    """Initialize nested submodules from exact-commit shallow caches."""
    if not (project / ".gitmodules").is_file():
        if dry_run:
            print("$ " + shlex.join(submodule_command()))
            return 0
        return run_process(submodule_command(), project, timeout=600, capture_output=False).returncode
    root = _cache_root(project)
    pending = [project]
    while pending:
        parent = pending.pop(0)
        pending.extend(_initialize_level(parent, root, dry_run))
    return 0
