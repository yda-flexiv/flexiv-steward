"""Exact-commit Git caches shared by submodules and external repositories."""

from __future__ import annotations

import hashlib
from pathlib import Path

from flexiv_steward.process import run_process


def cache_path(root: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:20]
    return root / f"{digest}.git"


def cache_branch(commit: str) -> str:
    return f"flexiv-steward-{commit}"


def is_git_cache(path: Path) -> bool:
    return (path / "HEAD").is_file() and (path / "objects").is_dir()


def has_commit(path: Path, commit: str) -> bool:
    if not is_git_cache(path):
        return False
    result = run_process(["git", "cat-file", "-e", f"{commit}^{{commit}}"], path)
    return result.returncode == 0


def ensure_cache(cache: Path, url: str, commits: tuple[str, ...]) -> None:
    """Fetch missing commits into a bare cache, one shallow branch per commit."""
    missing = tuple(commit for commit in commits if not has_commit(cache, commit))
    if not missing:
        return
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not is_git_cache(cache):
        run_process(["git", "init", "--bare", str(cache)], cache.parent, check=True)
        run_process(["git", "remote", "add", "origin", url], cache, check=True)
    else:
        run_process(["git", "remote", "set-url", "origin", url], cache, check=True)
    for commit in missing:
        # A local clone copies heads by default, so expose the exact commit
        # under a private cache branch.
        run_process(
            ["git", "fetch", "--depth=1", "--no-tags", "origin", f"+{commit}:refs/heads/{cache_branch(commit)}"],
            cache,
            timeout=600,
            check=True,
        )
