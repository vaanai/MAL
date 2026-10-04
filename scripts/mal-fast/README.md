# scripts/mal-fast

Fast-host (mal-fast-0) units and installers. Paper only.

## getBlock tip follower (DEC-015 section 2.2)

`mal-fast-tip-follower.service` runs `tools/fast_tip_follower.py`. It fetches every confirmed
slot with Helius getBlock, in order, and writes:

- `/var/lib/mal/sealed/fast-trades-tip/trades-<hour>.jsonl`, `creates-<hour>.jsonl`,
  `migrations-<hour>.jsonl` (walker rows, `source: "tip"`, `t_recv_ms` = local receive time);
- `/var/lib/mal/sealed/fast-creates-tip/observe-<UTC day>.jsonl`: creates in the observe unit's
  format (`stream`, `txType`, `mint`, `t_ws`, `traderPublicKey`, `signature`,
  `vSolInBondingCurve`, `vTokensInBondingCurve`);
- `gaps.jsonl` (backlog jumps, unfetchable slots, contradicted skips, dropped PumpSwap rows) and
  `skipped-slots-<hour>.jsonl` next to the trades.

The next hour's trades and migrations files and the next day's observe file are created empty at
about :59:55, because the runner's `DirectoryTail` opens a new file at its end.

Switching the runner is config only: set `tape_dir` to `/var/lib/mal/sealed/fast-trades-tip` and
`creates_dir` to `/var/lib/mal/sealed/fast-creates-tip` in `fast-forward-paper.json` after the
coverage check passes. `fast-forward-paper.json` still has the listener paths
(`fast-trades-live`, `fast-observe`); that change is not made yet. (JSON has no comments, so the
note lives here.)

Observe-format creates deliberately omit `event_ts`, `block_time`, `initialBuy` and `solAmount`.
`event_ts` and `block_time` would change the runner's create-time path from the one the md5
replay proof covered. EXP-012's features do not use `initialBuy` or `solAmount`.

Credits: the follower counts 1 credit per call (`--credits-per-call`, default 1). The Helius
dashboard confirmed about 1 credit per getBlock on this plan. The PumpSwap pool lookup
(getMultipleAccounts) goes through the same rate limiter and credit counter.

If the follower falls more than `--backlog-slots` (50, about 20 s) behind the tip it jumps to the
tip and records the skipped range in `gaps.jsonl` (`reason: "backlog_jump"`). It never stamps old
blocks with a fresh `t_recv_ms`.

The installer installs the unit but never enables or starts it. The key is read only through the
unit's `EnvironmentFile=`.
