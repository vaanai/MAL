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

## FLOOR lane decision host (floor-lane-1, DEC-035; PR-A)

`tools/floor_lane_host.py` is a keyless, paper-decision host that runs the frozen `l1-floor-v2` rule (`tools/arena_rule_l1.py`, FLOOR frozen sha `5e12f8f5`) and appends hash-chained `entry` /
`exit` rows (plus a `start` row with the full frozen sha, and `entry_refused_gap` evidence rows) to `<root>/decisions/l1-floor-v2/<day>.jsonl`. The separate executor
(`tools/floor_arena_executor.py`, claude/floor-lane-executor) reads that root as its `root`. It is not the G1 arena book: its own root, ledger and `bots.json` (config refuses any path inside
`/data/mal/arena`).

- **Any restart blocks all new FLOOR entries for about 24 h** (24 h hold + 1.9 s + 60 s cooldown + 1 s tick + 5 s margin, counted from the first live tick after the restart). Exits are not affected. Do not restart the host casually: plan restarts, and record each one with its instant.
- Unit (Helm installs; no worker starts it): one long-running process, `python tools/floor_lane_host.py run --config <json>`, from a clean checkout at the lane-host commit, `Nice=19`, about
  1.5 GB (ArenaState over a 365-minute warm-up plus the pool table). First, once: `... register --config <json>` (clock must be in the future).
- Feed path: `tape_dir` is the live arena tape directory on mal-fast-0, the byte mirror `/home/claude/mal-data/arena-mirror/tape` written by `tools/tape_mirror.py pull`; this host tails it,
  read-only. It is an added reader of the sealed mirror: a SEALS-BRIEF READERS line (manager) must exist before the first start. No sealed tip tape, no `bots.json` of the arena.
- Output: `root` (example `/var/lib/mal-live/floor-lane/decisions-root`), checkpoint `root/lane/checkpoint.json`, pool table `root/pooltable/`. Check the chain with `... verify --config <json>`.
- Gaps: a data-loss gap on the pumpswap feed (or on `all`), or this host's own downtime, inside the trigger's `gap_lookback_s` (default 6 h, range 6 to 24 h) refuses the entry and logs
  `entry_refused_gap`. A restart marks every open position `resume_reopen`, which the executor does not follow: no planned restarts while a position is open.
- First start (no checkpoint or no pool table) needs `run --since-hour 2026-11-01T00`: the pool table is built only from tape hours at or after it (FLOOR ages are 24 to 200 h, so set it at
  least 201 h before the first intended entry). A missing checkpoint beside an existing decision log refuses to start. `run` refuses a dirty checkout.
- Replay (decision-equivalence job): `... replay --config <json> --from-ms A --to-ms B --seed-from-hour H`, output root outside `/data/mal/arena`; H must be at least 201 h before A; prints the
  entry/exit md5.
