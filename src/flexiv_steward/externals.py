"""Cache-backed initialization of Flexiv external repositories."""

from __future__ import annotations

import shlex
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flexiv_steward.deps import declared_externals
from flexiv_steward.errors import ProjectError
from flexiv_steward.gitcache import cache_branch, cache_path, ensure_cache, has_commit
from flexiv_steward.models import ExternalSpec
from flexiv_steward.process import run_git, run_process
from flexiv_steward.submodules import cache_root, initialize_tree


def _external_cache_root(project: Path) -> Path:
    common = run_git(project, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common) / "flexiv-steward" / "externals-v1"


def _clone_from_cache(spec: ExternalSpec, cache: Path) -> None:
    if spec.path.exists():
        raise ProjectError(
            f"{spec.path} exists but is not a Git repository; remove it and retry"
        )
    spec.path.parent.mkdir(parents=True, exist_ok=True)
    run_process(
        ["git", "clone", "--branch", cache_branch(spec.commit), str(cache), str(spec.path)],
        spec.path.parent,
        timeout=600,
        check=True,
    )
    run_process(["git", "remote", "set-url", "origin", spec.url], spec.path, check=True)


def _ensure_checkout(spec: ExternalSpec, cache: Path) -> None:
    if not (spec.path / ".git").exists():
        _clone_from_cache(spec, cache)
        return
    if has_commit(spec.path, spec.commit):
        return
    run_process(
        ["git", "fetch", str(cache), f"+{spec.commit}:refs/heads/{cache_branch(spec.commit)}"],
        spec.path,
        timeout=600,
        check=True,
    )


def initialize_externals(project: Path, dry_run: bool) -> None:
    """Populate declared externals from a shared exact-commit cache."""
    specs = declared_externals(project)
    if not specs:
        return
    root = _external_cache_root(project)
    submodule_root = cache_root(project)
    pairs = [(spec, cache_path(root, spec.url)) for spec in specs]
    if dry_run:
        for spec, cache in pairs:
            print(f"$ cache {shlex.quote(spec.url)} at {shlex.quote(str(cache))} commit {spec.commit}")
            print(
                "$ "
                + shlex.join(
                    ["git", "clone", "--branch", cache_branch(spec.commit), str(cache), str(spec.path)]
                )
            )
            print("$ " + shlex.join(["git", "remote", "set-url", "origin", spec.url]))
            initialize_tree(spec.path, submodule_root, dry_run=True)
        return
    unique: dict[Path, tuple[str, list[str]]] = {}
    for spec, cache in pairs:
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
    for spec, cache in pairs:
        _ensure_checkout(spec, cache)
        initialize_tree(spec.path, submodule_root, dry_run=False)
