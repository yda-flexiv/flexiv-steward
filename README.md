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
```

All read-only commands discover the enclosing Git worktree by default. Use
`--project` to operate on a different worktree explicitly.
