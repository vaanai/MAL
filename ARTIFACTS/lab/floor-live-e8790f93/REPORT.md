# FLOOR live e8790f93 probe-clamp lift — independent review

**Pin reviewed:** `e8790f9342df6c59a849f2ed0897b59ad5441015`  
**Live today:** `3710e1af8cf434faca570046de896a7702464709` (`tools/floor_lane_executor.py` blob `2b0b109a6d6232e4de0feb16339ce13ccd4cd22f`)  
**e879 executor blob:** `6f353716e29f72c8f92699310d44f21ce23dd0dd` (same blob as parent `54051438`, PR #1297)  
**Scope:** read + adversarial tests in this workspace against a throwaway worktree at e879. No deploy, SSH, keys, trades, merges, production source edits, or pin change. This review does not authorize cutover and does not revise the live pin.

**Host facts used, not re-read here** (parent, 2026-11-09 18:18 PST): three matching real holdings, no pending/sells/failures/latches; wallet 1.009410681 SOL, realized 0; checkpoint seq 8/current, no gaps, A3 clear. Exact 17:16:26.008 PST `limit:max_open` refusal confirmed `not_sent`, zero fees.

**Verdict: GO for an executor-only same-config restart onto e879**, bounded on the assumptions below. Not a profit claim. No book has cleared the promotion gate. **Not a license to reset `first_send_ms`, the cursor, or the host.**

## GO, in one line

The 17:16:26.008 PST refusal is the probe DEC-019 wrapper (`Limits.__post_init__` clamps `max_open` 8→3). e879 lifts that in-memory in `FloorLanePort.__init__` (`max_open`→`cfg.max_open` = 8, `max_attempts` 60→100000, `max_days` 7→60) and does not rewrite state files. The outer 14-day canary from `first_send_ms` **1794148248897** (cutoff **2026-11-22T14:30:48.897Z**) and the cost-capped **−0.30 SOL** pre-send still refuse when the inner lift would send.

## What e879 actually changes

e879 itself is EXP-029 Erratum 1c + DEC-035 Am.2 (docs). The runtime lift is parent `54051438`. Retargeting a whole checkout from 3710 to e879 also pulls:

| Path | 3710→e879 | On FLOOR live send path? |
| --- | --- | --- |
| `tools/floor_lane_executor.py` | +9 runtime lines (constants + 3 `setattr`) | **Yes.** `mal-floor-lane.service` is `python -m tools.floor_lane_executor`. |
| `tools/floor_lane_host.py` | identical blob `a4387c24135f198f8b3ce7d75f810170fbace297` | Host process. Must not restart. |
| `tools/floor_arena_executor.py`, `probe_live.py`, `arena_live_bridge.py`, `h5_lane_executor.py`, `floor-lane-live.json.example` | byte-identical | Yes, imported; unchanged. |
| `tools/forge/dsl_lane_host.py`, `tools/forge/events.py` | changed (PR #1286) | **No.** Not imported by the executor or the host. |
| `tools/lane_daily_report.py` | added (PR #1284) | **No.** Same. |

EXP-029 Appendix A 38-file host+executor import closure at e879: **only** `tools/floor_lane_executor.py` differs from 3710. Executor-only closure is 34 files; none are `tools.forge*` or `tools.lane_daily_report`. Static import of `tools.floor_lane_executor` does not load those modules (`test_whole_checkout_does_not_import_forge_or_lane_daily_report`).

Retargeting the **FLOOR executor working tree** to e879 therefore does not introduce forge/daily-report behavior into `mal-floor-lane.service`. It would change on-disk forge/report files if those units share the tree; that is a different process and is not this GO.

Production delta on the executor (9 added lines):

```
LANE_PROBE_MAX_ATTEMPTS = 100_000
LANE_PROBE_MAX_DAYS = 60.0
object.__setattr__(ex.limits, "max_open", int(cfg.max_open))
object.__setattr__(ex.limits, "max_attempts", LANE_PROBE_MAX_ATTEMPTS)
object.__setattr__(ex.limits, "max_days", LANE_PROBE_MAX_DAYS)
```

`priority_lamports` overwrite (p155) is unchanged. `loss_cap`, `size_lamports`, and `end_ms` are not touched. `capped_pnl`, `first_send_ms`, `CANARY_MAX_MS`, `abandoned`, `_start_sell`, and `evaluate` are not in the plus-lines.

## True clamps at 3710 (why the incident happened)

`pe.Limits.from_config({max_open: 8, max_attempts: 60})` under the default `dec019` profile:

- `max_open = 3` (DEC019_MAX)
- `max_attempts = 60`
- `max_days = 7`
- construction `end_ms` is DEC-019 2026-10-12; `make_live_port` then setattr's `cfg.end_ms` (`1796688000000`, 2026-12-07 16:00 PST)

Live `check_buy` then `handle_signal` skip label is `limit:max_open` — not lane `lane_full`. With 3 open, a 4th never reaches the lane ledger. Unwrapped `make_live_port` at e879 still clamps 3/60/7; only `FloorLanePort` lifts.

## Outer 14-day and −0.30 still bind (most important)

Clocks from live `first_send_ms` **1794148248897** = 2026-11-08 06:30:48.897 PST. **This review must not reset or extend that field.**

| Gate | Instant | Still effective after inner 60d / 100000? |
| --- | --- | --- |
| Probe 7-day (3710, lifted away) | 2026-11-15 14:30:48.897Z | Inner `check_buy` at e879 returns None at day 8 and at attempt 61. |
| **Canary 14-day** (`CANARY_MAX_MS`, write-once `first_send_ms`) | **2026-11-22 14:30:48.897Z** | `FloorBridge` `canary_expired` when `now > first + 14d`. Day 20: inner would send (attempts=61, max_days=60, before `end_ms`); wrapped entry is `canary_expired`, `buys=[]`, `first_send_ms` unchanged. Entry 1 ms before the cutoff is not expired. |
| `end_ms` | 2026-12-08 00:00Z / Dec 7 16:00 PST | Unchanged. Refuses at `END_MS`. Inner 60d would be 2027-01-07, after both canary and `end_ms`. |
| Cost-capped −0.30 | every pre-send | Inner attempt 61 is allowed by `check_buy`; `FloorLaneLedger.evaluate` still returns `lane_budget_presend` on the +100% seven-fill wipe that would go through −0.30. `8 × (30e6 + 3.86e6) = 270_880_000 < 300_000_000`. |

`first_send_ms` is written only when it is `None` (`floor_arena_executor.handle_entry`). Restart with the live sidecar already set to 1794148248897 does not extend the Nov 22 cutoff. The Erratum 1b Helm nulling of that field is a **separate**, later window-reset; it is **not** part of this GO.

## What the lift does not remove

Byte-identical vs 3710 and still bound in tests at e879:

- p155 overwrite (`F.PRIORITY_LAMPORTS = 155_000`)
- `pl.SELL_MAX_ATTEMPTS = 10` / `abandoned` (100 landed-failed sells → 10 distinct sell signatures, then abandoned; every sell CU 155k)
- cost-capped pre-send, 0.10 daily marked stop, wallet floor, `LANE_MAX_OPEN` 8 / `lane_full`, `max_trades_per_day` 30, A3 pause, STOP/HALT, LIVE_OK
- recovery / same-config restart: probe `open` + `first_attempt_ms` + attempt count persist; lift is re-applied in `__init__` (in-memory; state files not rewritten)

100000 / 60 are dummy inner caps so `pe.check_buy` does not bind before the lane gates. They are not unbounded trading.

## State compatibility (3 open, seq 8, first_send 1794148248897)

- Patch does not rewrite `bridge-state-live.json`, `floor-state-live.json`, the probe state file, the ledger, or the checkpoint.
- 3 open is legal under both the 3710 clamp (3) and the lifted cap (8). After restart, a 4th through 8th **can send** if canary / budget / daily / LIVE_OK / A3 all pass. That is the incident fix, not a side effect.
- Cursor / seq 8 / `prev` survive `FloorLaneBridge` reconstruct (test). Parent: seq 8 current, no gaps.
- Do not restart `mal-floor-lane-host` (24 h entry block). Do not copy dry orders. Do not empty live state.

## Assumptions this GO needs

1. Restart target is `mal-floor-lane.service` only (`python -m tools.floor_lane_executor`), same config JSON, same `LIVE_OK`, same bot_id, same `end_ms` 1796688000000, same stake/budget/daily/max_open.
2. Working tree at e879 (or any sha whose Appendix A blobs match e879). Whole-checkout retarget is OK **for this unit** because forge/daily-report are not imported. Do not treat that as approval to restart forge DSL host off the same tree without its own review.
3. `floor-state-live.json` `first_send_ms` stays **1794148248897**. Cursor/seq/prev/`start_seen` untouched. 1108-1 clock reset remains unapproved.
4. Parent 18:18 PST inventory (3 matching holdings, no pending/sells/failures/latches, A3 clear) still holds at the restart instant. This review did not SSH.
5. Helm applies the existing pin/restart path. **This agent is not authorized to deploy or to revise the pin.**

## NO-GO cases (not this patch, but they remain)

- Resetting `first_send_ms` as part of this restart (would extend Nov 22).
- Restarting the decision host.
- Empty live state or copying dry orders (INIT-CUTOVER.md).
- Editing the 9 lines further, or raising `CANARY_MAX_MS` / `LANE_BUDGET_MAX_LAMPORTS`.
- Calling 100000 / 60 “the new lifetime.” They are not.

## Tests

Run from a checkout/worktree **at e879** so `tools.floor_lane_executor` is blob `6f353716` (workspace `HEAD` on this review branch still has the 3710 executor).

```
cd /tmp/mal-e879   # git worktree at e8790f93
python3 -m pytest ARTIFACTS/lab/floor-live-e8790f93/test_floor_live_e879_clamps.py -q
# 23 passed
python3 -m pytest ARTIFACTS/lab/floor-live-budget-3710e1af/test_floor_live_budget_safety.py \
  ARTIFACTS/lab/floor-live-budget-3710e1af/test_floor_live_init_cutover.py -q
# 29 passed, 2 failed: the 3710 blob-pin tests (expected; executor is now 6f353716)
```

Logs: `pytest.out`, `pytest-prior31-at-e879.out`. Diff: `floor_lane_executor.3710-e879.diff`.

Critical new cases: `test_inner_60d_and_100k_would_allow_day20_but_outer_14d_canary_refuses`, `test_inner_100k_does_not_bypass_030_lifetime_presend`, `test_floor_live_import_closure_only_floor_lane_executor_differs_from_3710`, FakeRpc 4th-through-8th / 9th `limit:max_open`, attempt 61, day 8, unwrapped still 60/7d, restart re-lift, 10 abandoned p155 sells.
