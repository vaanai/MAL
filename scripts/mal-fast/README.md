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
- Tape dir for the 201 h+ history: `tape_dir` = `$HOME/mal-data/floor-tip/tape` (arena layout), kept by the MiScusi job `scripts/ops/floor-tip-linkdir.sh` (hard links of the arena tip writer's dir, plus closed hours backfilled from research-0; `tools/floor_tip_linkdir.py`; status in `<tape_dir>/.linkdir/status.json`). Like the mirror above it is an added reader of a derived tip copy: the SEALS-BRIEF READERS line must name it.
- Output: `root` (example `/var/lib/mal-live/floor-lane/decisions-root`), checkpoint `root/lane/checkpoint.json`, pool table `root/pooltable/`. Check the chain with `... verify --config <json>`.
- Gaps: a data-loss gap on the pumpswap feed (or on `all`), or this host's own downtime, inside the trigger's `gap_lookback_s` (default 6 h, range 6 to 24 h) refuses the entry and logs
  `entry_refused_gap`. A restart marks every open position `resume_reopen`, which the executor does not follow: no planned restarts while a position is open.
- First start (no checkpoint or no pool table) needs `run --since-hour 2026-11-01T00`: the pool table is built only from tape hours at or after it (FLOOR ages are 24 to 200 h, so set it at
  least 201 h before the first intended entry). A missing checkpoint beside an existing decision log refuses to start. `run` refuses a dirty checkout.
- Replay (decision-equivalence job): `... replay --config <json> --from-ms A --to-ms B --seed-from-hour H`, output root outside `/data/mal/arena`; H must be at least 201 h before A; prints the
  entry/exit md5.

## FORGE DSL lane decision host (FORGE-SPEC Part C, PR-A)

`tools/forge/dsl_lane_host.py` is a keyless, paper-decision host that runs ONE FORGE DSL spec (`tools/forge/evaluate.validate_spec`) on an arena-layout tape dir and appends hash-chained
`start` / `entry` / `exit` (and `entry_refused_gap` evidence) rows to `<root>/decisions/<bot_id>/<day>.jsonl`, the layout `tools/floor_arena_executor.Bridge` reads. One process per spec, one `bot_id`
per spec. It reads no key and sends nothing. A shortlisted spec still needs a DSL-bound executor (see "Executor gap") before it can trade; this host is the decision half, built ahead of need.

- Features, thinning, gap exclusion, cooldown, one_per_pool and the exit rules are the evaluator's own code paths (`events.parse_lines`, `events.compute_features`, `evaluate.simulate_paths`);
  `tools/test_forge_dsl_lane_host.py` proves entries, exit times and exit reasons equal `evaluate.run_trades` on a synthetic tape (same pools, same event instants, same exec instants, same reasons)
  for the base spec and five variants (tp-only, sl+trail, no one_per_pool, venue 1, venue 0 with a curve completion).
- Price convention: PumpSwap rows PRE-trade with Q = quote_reserve + V, curve rows POST-trade; the path spot is the post-trade spot of the pool's last print at or before each grid time after the
  fill print (first print >= 2.0 s after the event). tp/sl/trail trigger at grid point k and execute at k+1 (>= 5 s of exit lag); `exit` rows carry `grid_ms` (trigger point) and `exec_ms` (the
  evaluator's exit instant); row `ms` is the host clock when the decision was made.
- Steps, once per spec, as the manager: (1) write the spec JSON and a config (`dsl-lane-host.json.example`, `dsl-lane-spec.json.example`); `root` must be new and outside `/data/mal/arena`.
  (2) `python tools/forge/dsl_lane_host.py register --config <json>`: pins the spec sha256 and the sha256 of the forge files, `arena_stack.py` and `arena_pool_table.py` in `<root>/registry.json`.
  (3) Keyless dry of a few hours: `python tools/forge/dsl_lane_host.py run --config <json> --since-hour <H>` as a MiScusi job on mal-fast-0, `Nice=19`, from a clean checkout. `--since-hour` is
  required and should be at least 7 h before the start (feature history is 6 h; nothing older is opened). Entries decided before the process start are SHADOW (state only, no rows).
  (4) Check the chain: `... verify --config <json>`. Closed-slice replay for a decision md5: `... replay --config <json> --since-hour H --from-ms A --to-ms B` (writes into `root`; use a scratch root).
- Tape: `tape_dir` is the arena-layout dir, for floor-lane-1 `$HOME/mal-data/floor-tip/tape` (SEALS-BRIEF FLOOR LANE READERS (a)-(e)). A new bot reading it, or any other dir, needs its own READERS line
  from the manager before the first start.
- Safety: `run` refuses a dirty or unknown checkout (the start row's `frozen_sha` is the full HEAD sha and its binding holds the file sha256s); refuses a registry or pin mismatch; refuses a
  decision log without its checkpoint (the start also writes one at once). Downtime is a `host_down` span. A restart closes every entry still open in the log with an `exit` row, reason
  `restart_close` (the executor must sell on it); the replay then holds those pools with shadow positions. An entry is dropped (row `entry_refused_gap`) when a data-loss gap on its venue
  (or `all`), or a host_down span, overlaps `[t_event - gap_lookback_s, fill]`; the default lookback is the longest feature window the spec uses (min 60 s). The entry row is written when the
  fill print is seen (>= 2 s after the event). Row `ms` is monotone in the chain; event and fill instants are in `meta`. Specs that use `age_s` or `mig_age_s` are refused.
- Follower: the two newest tape hours are re-read on every poll (the tip writer keeps an hour open ~120 s after the next one starts); older hours are read once, one hour at a time; a missing
  hour is injected as a data-loss gap on both venues; a `.jsonl` compacted away mid-read is retried as `.jsonl.zst`. A row that arrives after later rows is history only.
- Not a subset of the evaluator's book. Filters applied before entry like the evaluator (a fill exists, valid fill state, fill-state real quote >= 2 SOL). What can still differ, by source: a gap
  row that reaches the tape after the event or grid point (host may enter or hold where the evaluator excludes; direction unknown), late rows (history only), the evaluator's split-end
  censoring (host has none), and an event inside (exec, decision] of a prior position (host takes fewer). The tests count host-only entries against the evaluator on a tape with no-fill and
  low-fill-quote pools (0). NOT tested: gap rows that arrive late, real tape, throughput on real tape (about 2,000 to 3,000 lines/s measured on the synthetic tape), a wall-clock tick.
- Live vs backtest, forced exit: a host restart closes every open entry early with `restart_close`; the evaluator has no such exit, so a live position can end before its own exit rule fires.
- No wall-clock tick: exits are decided when trade rows arrive. A feed stall leaves open positions without an exit row and without a stall gap; the executor's clock backstop at
  `decision_ms + max_hold_s` (see the executor gap) is the only cover then, and the host should not run without the tape writer's own health check.
- Executor gap (nothing is changed in this PR): `tools/floor_lane_executor.py` / `floor_arena_executor.FloorBridge` are bound to bot `floor-lane-1`: `make_config` pins `bot_id` and FLOOR's frozen sha, the
  entry pin requires `stake_sol == 0.25` and `meta` to carry `rule == l1-floor-v2`, `cell`, `hold_s`, `tp`; `FOLLOWED_EXIT_REASONS` is only `tp_1.5x` and `time_24h` (every other exit reason is
  counted `exit_not_followed`); `backstops()` sells at `decision_ms + 24 h + 1.9 s`; `FLOOR_LANE_STAKE_BOUNDS_LAMPORTS` allows 0.02 to 0.03 SOL. A DSL executor needs a sibling subclass of the base
  `arena_live_bridge.Bridge` (whose `handle_exit` already follows any reason string): pin `bot_id` and the start row's `frozen_sha` (= host HEAD) and `spec_sha256`; replace the meta pin by
  `meta.rule == "dsl"`; follow `tp, sl, trail, time, migrated, restart_close`; treat a curve-venue `pool` as the mint string (`mint_for_pool` must handle it); take the stake from the row (`stake_sol`) inside the lane bounds; set the clock backstop to
  `decision_ms + max_hold_s` plus a margin (from `meta.max_hold_s`) instead of 24 h; and own a lane bot_id in the shared lane ledger. The sl/trail/tp sells then come from the host's exit rows
  (the executor does no price tracking of its own), so a live sell lags the trigger by the host's detection time (the next tape row after the grid point) plus the sell build.

## FORGE DSL lane executor (FORGE-SPEC Part C, PR-B; DEC-035, one lane bot_id per spec)

`tools/forge/dsl_lane_executor.py` follows the decision rows of one `tools/forge/dsl_lane_host.py` spec and trades them in the DEC-035 lane. It closes the "Executor gap" above. A sibling of
`floor_lane_executor` (same `Bridge`, `LaneLedger`, `FloorLanePort`, ATA close, A3 pause, STOP/HALT); nothing is re-decided. Files: `dsl-lane-executor.json.example` (dry; fill in the pins).
Neither merging nor this runbook installs a unit, LIVE_OK or a credential. Only the manager starts a lane book; Helm installs the unit, LIVE_OK and the credential; no Claude session reads the wallet key.
It needs a SEALS-BRIEF READERS line from the manager before any start (it reads the host's decision log, the same as the FLOOR executor reads FLOOR's).

- **One executor per spec.** The host config `bot_id` = executor config `bot_id` = `lane.bot_id` = the decision-log arm = the LIVE_OK content (`/etc/mal-arena-live/<bot_id>/LIVE_OK`, root-owned, content exactly
  the bot_id). `floor-lane-1`, `h5-lane`, `l1-floor-v2`, `hv4-*` and ids with `:` or `/` are refused. Unit: `python -m tools.forge.dsl_lane_executor run --config <json> [--live]`, user `mal-live`.
  Give each bot its own `state_dir`; all bots share the lane ledger and STOP of the h5-lane, so lane_full, the -0.30 SOL budget and the -0.10 SOL daily stop are lane-wide.
- **Pins (a mismatch refuses, nothing is sent):** `frozen_sha` = the host's checkout HEAD (40 hex, from the host's start row); `spec_sha256` (64 hex, as printed by `dsl_lane_host.py register`); `max_hold_s` = the
  spec's `exit.max_hold_s`. The start row's `frozen_sha` / `spec_sha256` must equal them (a mismatch latches `frozen_sha_mismatch` / `spec_sha_mismatch`, also on a later start row). Every entry row needs
  `meta.rule == "dsl"` and `meta.max_hold_s` equal to the config, `arm` equal to the bot_id, and `meta.spec_sha256` (if present) equal to the config; `meta.venue == 1` (PumpSwap): a bonding-curve entry is refused `venue_not_pumpswap`, the live port buys PumpSwap pools only.
- **Exits:** every host reason is followed: `tp, sl, trail, time, migrated, restart_close` (the host's close of an entry open across its restart; an exit row for an order whose buy has not landed waits for the landing and then sells). Any other reason is counted
  `exit_not_followed`. Backstop: `decision_ms + meta.max_hold_s + DSL_BACKSTOP_MARGIN_MS` (one 300 s grid step + 60 s = 360 s; the entry row is written at the host's fill), reason `time_backstop`, so the host's own time exit normally wins.
  No stop loss of the executor's own: the base bridge's `sl_backstop` is not used. A live sell lags the host's trigger by the host's detection time plus the sell build.
- **Stake:** `lane.stake_lamports` in `DSL_LANE_STAKE_BOUNDS_LAMPORTS` (0.02 to 0.03 SOL, one constant, pinned by `test_constants_follow_dec035_s2_and_the_host`). The row's `stake_sol` is not used for sizing; it is logged as `paper_stake_sol`.
- **Same limits as FLOOR:** STOP before every send, ledger lock and read before every send, ATA close checked at start, `ata_rent_not_refunded` latch, A3 redeploy pause (`a3_record_file` required for `--live`), per-bot-id process lock.
  Limits come from `floor_lane_executor`'s ledger class and constants at call time, so a lane-wide change (cap, daily presend) applies here too.
- **Host restart (a second start row, e.g. host_down):** new entries are held 10 min (`gap_hold`, persisted in `dsl-state-<mode>.json`); open positions are untouched and keep the host's later exit rows and the backstop. Exit rows are followed whatever their `ms` (no ordering assumed).
- **Check:** `python -m tools.forge.dsl_lane_executor check --config <json> [--live]`.
- **Not tested here:** a real send, the real feed or RPC, the wallet, a real host run feeding this executor end to end, and the real behaviour of a restarted host for a position open across the restart (tested only with synthetic start/exit rows). The first read of real fills is the lane's day-1 report (DEC-035 s5). Nothing here says any spec makes money.

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
- **Redeploy pause (DEC-035 s4):** with `a3_record_file` (required for `--live`) the executor reads the newest A3 daily record; a pins_changed or program_changed flag, or a stale, future or unreadable record, pauses new entries (reason `a3_pause:...`, not a latch; it clears when the record clears after the manager re-pins). Exits keep running. Confirm the record path on the host.
- **Kill:** `touch /var/lib/mal-live/h5-lane/STOP` stops new entries at once (open positions keep the rule's exits: tp +50 %, 24 h). The budget stop writes the same file and it survives restarts.
  `systemctl stop mal-floor-lane` stops the process; open positions are persisted in the state dir and resume on the next start (the 24 h deadline is stored with each order).
- **Not tested here:** a real send, the feed, the Helius RPC, the wallet. The first read of real fills is the lane's day-1 report (DEC-035 s5).
