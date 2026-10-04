# scripts/mal-fast

Fast-host (mal-fast-0) units and installers. Paper only.

## getBlock tip follower (DEC-015 section 2.2)

`mal-fast-tip-follower.service` runs `tools/fast_tip_follower.py`. It fetches every confirmed
slot with Helius getBlock and writes hourly `trades-`, `creates-` and `migrations-` files to
`/var/lib/mal/sealed/fast-trades-tip`, with `t_recv_ms` set to the local receive time.

`fast-forward-paper.json` still has `"tape_dir": "/var/lib/mal/sealed/fast-trades-live"`. After the
coverage check passes, `tape_dir` will point at `/var/lib/mal/sealed/fast-trades-tip`. That
change is not made yet. (JSON has no comments, so the note lives here.)

Open point: the runner reads creates from `creates_dir/observe-<day>.jsonl` (the listener
file), not from the follower's `creates-<hour>.jsonl`. See the PR body.

The installer installs the unit but never enables or starts it. The key is read only through the
unit's `EnvironmentFile=`.
