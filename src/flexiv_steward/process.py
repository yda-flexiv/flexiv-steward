"""Centralized subprocess execution with consistent diagnostics and timeouts."""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from flexiv_steward.errors import ProjectError


LOG = logging.getLogger(__name__)
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    cwd: Path
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def output(self) -> str:
        return (self.stdout + self.stderr).strip()


def run_process(
    args: Sequence[str],
    cwd: Path,
    *,
    timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
    capture_output: bool = True,
    env: Mapping[str, str] | None = None,
    check: bool = False,
) -> CommandResult:
    """Run one command and normalize timeout, logging, and error reporting."""
    command = tuple(str(value) for value in args)
    directory = cwd.resolve()
    LOG.debug("running %s in %s", shlex.join(command), directory)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=directory,
            check=False,
            capture_output=capture_output,
            text=True,
            timeout=timeout,
            env=dict(env) if env is not None else None,
        )
    except FileNotFoundError as error:
        if error.filename == command[0]:
            raise ProjectError(f"command not found: {command[0]}") from error
        raise ProjectError(f"command could not start in {directory}: {error}") from error
    except subprocess.TimeoutExpired as error:
        duration = time.monotonic() - started
        LOG.warning("command timed out after %.1fs: %s", duration, shlex.join(command))
        raise ProjectError(
            f"command timed out after {timeout:g}s in {directory}: {shlex.join(command)}"
        ) from error
    duration = time.monotonic() - started
    result = CommandResult(
        command,
        directory,
        completed.returncode,
        completed.stdout or "",
        completed.stderr or "",
        duration,
    )
    LOG.debug("command exited %d after %.3fs: %s", result.returncode, duration, shlex.join(command))
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise ProjectError(f"{shlex.join(command)} failed in {directory}: {detail}")
    return result


def run_git(
    project: Path,
    *args: str,
    timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Run Git and return stripped standard output or raise ``ProjectError``."""
    return run_process(["git", *args], project, timeout=timeout, check=True).stdout.strip()


def merged_environment(**updates: str) -> dict[str, str]:
    """Return a subprocess environment without mutating the caller's environment."""
    return {**os.environ, **updates}
