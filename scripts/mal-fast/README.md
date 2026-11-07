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

## FLOOR lane executor (DEC-035, bot_id `floor-lane-1`)

Runbook for `tools/floor_lane_executor.py`, the FLOOR book `l1-floor-v2` (frozen sha 5e12f8f5, rule `tools/arena_rule_l1.py`, decisions unchanged) sending real orders at micro size
in the live-learning lane. Files: `floor-lane-live.json.example`, `mal-floor-lane.service.example` (neither is installed by merging). Only the manager starts the book; Helm installs
the unit, LIVE_OK and the credential; no Claude session reads the wallet key.

- **Unit:** `mal-floor-lane.service` (user `mal-live`, `-m tools.floor_lane_executor run --config <json>`). It tails the decision log of the FLOOR v2 host (config `root`, read-only).
- **Paths:** state `/var/lib/mal-live/floor-lane/state` (bridge state, floor sidecar, probe state and fills); the shared lane ledger and the lane STOP file are the h5-lane ones
  (`/var/lib/mal-live/h5-lane/lane-ledger.jsonl`, `.../STOP`), so the 3-open limit and the loss limits are lane-wide. Wallet-wide `/var/lib/mal-live/STOP` and `HALT` are honoured too.
- **Dry vs live:** all three must hold for a send: config `"mode": "live"`, the `--live` flag in the unit (drop-in `live.conf`), and a root-owned `LIVE_OK` reading exactly `floor-lane-1`.
  Any one missing (a live config without `--live`, `--live` on a dry config, a missing LIVE_OK) refuses to start (exit 2, no restart loop). The example is `"mode": "dry"`: a `DryPort`,
  and the lane ledger is neither read nor written.
- **Check before start:** `python -m tools.floor_lane_executor check --config <json> [--live]` validates the config and the start gates; it sends nothing.
- **Limits in code (DEC-035 s2):** stake bounds `FLOOR_LANE_STAKE_BOUNDS_LAMPORTS` (default 0.02 to 0.03 SOL; the unit refuses to start outside them; raising them is one edit in that constant plus
  `test_constants_follow_dec035_s2` in `tools/test_floor_lane_executor.py`, after an owner decision), lane total -0.30 SOL, daily -0.10 SOL, at most 3 open across the lane (`lane_full`), STOP before every
  send, ledger lock and read before every send. A landed sell that refunds no token-account rent latches `ata_rent_not_refunded` (entries stop, exits run).
- **Kill:** `touch /var/lib/mal-live/h5-lane/STOP` stops new entries at once (open positions keep the rule's exits: tp +50 %, 24 h). The budget stop writes the same file and it survives restarts.
  `systemctl stop mal-floor-lane` stops the process; open positions are persisted in the state dir and resume on the next start (the 24 h deadline is stored with each order).
- **Not tested here:** a real send, the feed, the Helius RPC, the wallet. The first read of real fills is the lane's day-1 report (DEC-035 s5).
