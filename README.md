# flexiv-steward

`fs` is a small command-line helper for repeatable Flexiv worktree, build,
test, and agent-handoff workflows.

```sh
fs --project ~/ws/flexiv_sw/v4.x_develop context
fs context --json
fs wt doctor
fs wt sync RemoveSync2 --dry-run
fs builds
fs builds gui RelWithDebInfo
fs deps doctor --strict
fs setup rca --dry-run
fs gc builds --dry-run
fs gc builds
fs build all --configure
fs build rca --target RobotControlApp
fs test workflow --dry-run
fs session handoff --days 3
```

All read-only commands discover the enclosing Git worktree by default. Use
`--project` to operate on a different worktree explicitly.

`fs session handoff` reads local Codex, OpenCode, Cursor, and Copilot records
only for the selected worktree. It prints a bounded, redacted summary; it never
uploads transcripts or writes them into the repository.

`fs wt sync` refuses dirty worktrees, fetches the base ref, and merges it. If
Git reports conflicts, it leaves the merge in place and prints the conflicted
files plus the commands to resolve or abort it.

`fs wt doctor` reports each worktree's last active time using its latest HEAD
reflog entry or newer uncommitted-file modification. It does not traverse
ignored build outputs.

`fs builds` inventories the primary CMake caches under the source tree's `build`
directory and reports an exact preset only when its recorded cache variables
match; otherwise it identifies the build as custom/modified and shows key cache
options. Use
`fs builds gui` for a numbered terminal selector, or pass a directory/basename
to open that configured cache directly in `cmake-gui`.
`fs builds gui --dry-run` prints its command even when `cmake-gui` is not
installed.

`fs deps doctor` compares direct external repositories (including
`external/proto`) with the `GIT_TAG` declared in the top-level CMake project,
and checks every recursive Git submodule. Use `--strict` in an agent or build
preflight to fail on missing or mismatched revisions. `fs build` runs the same
version check by default; `--skip-deps-check` is an explicit escape hatch. Add
`--verbose` to list every clean dependency revision.

For a newly created worktree, use `fs setup rca` (or another profile). It
initializes recursive submodules, runs CMake configure so declared externals
are checked out, and then verifies all dependency revisions.

Submodule objects are cached below the superproject's common Git directory at
`flexiv-steward/submodules-v2`. Caches are keyed by remote URL, contain only
the exact commits pinned by the superproject, and are prefetched concurrently.
They survive linked-worktree removal; warm setup therefore works entirely from
the local cache without depending on a long-lived checkout.

`fs gc builds` deletes build and install artifacts from every other worktree
in the same Git repository, regardless of age, dirty state, or worktree lock.
It preserves the enclosing worktree (or the one selected with `--project`).
It removes `build/`, `install/`, `build_yocto/`, and `cmake-build-*/` directories
at the worktree root and, for wrapper layouts, under `flexiv/`.
Symlinked roots are skipped, and directories containing tracked files or another
worktree are refused. Source files and external install locations are preserved.
Use `fs gc builds --dry-run` to preview the paths and `--json` for machine-readable
results. Both preview and cleanup display each directory's disk usage and the
total, measured before deletion. JSON includes `size_bytes` keyed by directory
path and `total_bytes`.

Development checks use strict mypy:

```sh
uv run mypy
uv run python -m unittest discover -s tests -v
```

See [docs/performance.md](docs/performance.md) for the measured submodule and
build-directory results.

Both configure and build run through `docker/docker_dispatch.sh exec <platform>
bash -c`, from the CMake source directory. Steward detects both the `flexiv_sw`
wrapper layout (source under `flexiv/`) and a direct `flexiv_sw_base` worktree
(source at the repository root). In a direct base worktree, the `all` and
`yocto-all` profiles select the corresponding `*-relwithdebinfo-g1` presets.
This keeps CMake configure and incremental builds in the same Docker environment.
