# flexiv-steward

`fs` is a small command-line helper for repeatable Flexiv worktree, build,
test, and agent-handoff workflows.

```sh
fs --project ~/ws/flexiv_sw/v4.x_develop context
fs context --json
fs wt doctor
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
