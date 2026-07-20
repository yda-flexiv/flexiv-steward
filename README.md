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

`fs builds` inventories the primary CMake caches under `flexiv/build` and
reports an exact preset only when its recorded cache variables match; otherwise
it identifies the build as custom/modified and shows key cache options. Use
`fs builds gui` for a numbered terminal selector, or pass a directory/basename
to open that configured cache directly in `cmake-gui`.

`fs deps doctor` compares direct external repositories (including
`external/proto`) with the `GIT_TAG` declared in the top-level CMake project,
and checks every recursive Git submodule. Use `--strict` in an agent or build
preflight to fail on missing or mismatched revisions. `fs build` runs the same
version check by default; `--skip-deps-check` is an explicit escape hatch.
