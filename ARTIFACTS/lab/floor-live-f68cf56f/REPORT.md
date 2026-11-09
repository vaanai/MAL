# FLOOR live f68cf56f full-wipe presend — independent review

**Pin reviewed:** `f68cf56fcce8b1127e7159d8069506f0988eb95d` (PR #1303 merge; executor blob `556d15623459d56c8db706e2a2a5b2b2257cd577`)  
**Live today (parent):** `e8790f9342df6c59a849f2ed0897b59ad5441015` (executor blob `6f353716e29f72c8f92699310d44f21ce23dd0dd`)  
**Host blob both pins:** `a4387c24135f198f8b3ce7d75f810170fbace297` (byte-identical; host is not imported by the executor and is not needed for this pin)  
**Scope:** read + adversarial tests. No deploy, SSH, keys, trades, merges, or production source edits. This review does not authorize cutover and does not revise the live pin.

**Live facts used, not re-read here** (parent): since 2026-11-09 02:46:27 AM PST every new entry `lane_budget_presend` (16 through 04:33 AM). 6 real buys of 0.03, 1 TP sell realized +0.018778152 SOL, 5 open, 0 pending. Marks stale (>60 s). Fold pnl about −0.1396 SOL; PR #1303 cash **−139,591,048** lamports.

**Verdict: GO for an executor-only same-config restart onto f68c**, bounded on the assumptions below. Not a profit claim. No book has cleared the promotion gate.

## GO, in one line

e879 double-counts each filled open position whenever its mark is below stake (stale/missing = 0): cash already holds the buys, then `capped_pnl − (open_stakes + new stake + reserve)` subtracts the stakes again. Live arithmetic is **−342,751,048** vs −0.30, so entries refuse. f68c’s `wipe_floor − (new stake + reserve×(open+1))` is **−192,751,048**, inside −0.30. Unrealized gains still add no headroom. The 14-day canary from `first_send_ms` **1794148248897** (cutoff **2026-11-22 06:30:48.897 AM PST**) is unchanged.

## (1) Double-count — confirmed, reproduced on e879

`lane_fold` with marks older than 60 s sets open value to 0, so `view.pnl == view.cash`. Cash already includes every filled buy’s `sol_out`. e879 then still subtracts `view.open_stakes`.

Live-shaped book (cash −139,591,048, 5 filled opens, no marks, new stake 30e6, reserve 3.86e6 × 6):

| Formula | Lamports | e879 `evaluate` | f68c `evaluate` |
| --- | --- | --- | --- |
| User: −0.1396 − 5×(0.03+0.00386) − (0.03+0.00386) | **−342,751,048** | `lane_budget_presend` | n/a |
| e879: `capped_pnl − (open_stakes + stake + reserve×6)` | **−342,751,048** | `lane_budget_presend` | same number still computable |
| Full wipe: `cash − stake − reserve×6` | **−192,751,048** | (not used) | `None` (allowed) |
| User “all 6 wipe”: 6×0.03386 − 0.018778152 | 184,381,848 loss | — | f68c is ~8.37M more conservative: extra fees already in cash |

Imported e879 blob `6f353716` (worktree `/tmp/mal-e879-live`): `evaluate` → `lane_budget_presend`; no `wipe_floor`. Fold of 6 fills + 1 exit constructed to the live cash, stale +100% marks: `pnl == cash`, 5 open, same refusal on e879 / same allow on f68c.

The open cost is subtracted twice. The user’s reading is correct.

## (2) e879 → f68c diff and import closure

Runtime tools besides `tools/floor_lane_executor.py` are byte-identical, including `floor_lane_host.py`, `floor_arena_executor.py`, `probe_live.py`, `arena_live_bridge.py`, `h5_lane_executor.py`, `floor-lane-live.json.example`.

Executor production delta is the presend only: `_capped_value` split, `wipe_floor = cash − unfilled_stakes`, `evaluate` uses `wipe_floor − (stake + reserve)` instead of `capped_pnl − (open_stakes + stake + reserve)`. Probe-clamp lift (3→8 / 60→100000 / 7→60), p155 overwrite, `first_send_ms`, canary, abandoned, `_start_sell` are not in the plus-lines.

Static import closure of `tools.floor_lane_executor` at f68c vs e879: **only** `tools/floor_lane_executor.py` differs. `tools.forge*` and `tools.lane_daily_report` are not imported. `tools.floor_lane_host` is not imported by the executor; its blob is unchanged, so the decision host need not restart and is not needed for this pin.

DEC-035 Amendment 3 is in the f68c tree (docs). `h5_lane_executor.LaneLedger.evaluate` still uses the old formula; no H5 lane bot runs.

## (3) Full-wipe bound still refuses a −0.30 breach

`wipe_floor` is cash minus unfilled intents (filled buys are already in cash). Marks, including +100×, do not change it. Property-tested (3,000 random books): every accepted entry satisfies `cash − unfilled − new_stake − reserve×(open+1) > −300e6`; every `lane_budget_presend` is the complementary inequality. Re-evaluating accepted books with marks stripped still accepts.

Still refused: the 3710 seven-at-+100% eighth whose wipe would go through −0.30; exact boundary `cash + 1` allowed / `cash` refused; realized +18.778M then a later realized loss large enough to cross −0.30; unfilled intent charged once (not via cash). 8 × (30e6+3.86e6) = 270,880,000 < 300e6. 10 landed-failed sells still produce 10 distinct p155 signatures then `abandoned` (FakeRpc; `probe_live` unchanged).

## (4) Other rails unchanged

| Rail | f68c vs e879 |
| --- | --- |
| 14-day canary from `first_send_ms` 1794148248897 → **2026-11-22 06:30:48.897 AM PST** | Unchanged (`CANARY_MAX_MS`; write-once; `canary_expired` at +14d+1) |
| `end_ms` 1796688000000 (Dec 7 16:00 PST) | Unchanged |
| 0.10 daily pre-entry (`lane_daily_stop` on marked P&L / cost vs 00:00Z snap) | Unchanged; still no FLOOR daily presend |
| `LANE_MAX_OPEN` 8 / `lane_max_open` before presend | Unchanged |
| wallet floor | Unchanged (`floor_arena_executor.wallet_floor_lamports`) |
| A3 pause / LIVE_OK | Unchanged in source; not in the plus-lines |
| Probe lift 8 / 100000 / 60, p155 | Still present |

## (5) State compatibility (5 open, seq retained)

Patch does not rewrite `bridge-state-live.json`, `floor-state-live.json`, probe state, ledger, or checkpoint. 5 open is legal under 8. Cursor/seq/`first_send_ms` survive `FloorLaneBridge` reconstruct (test uses seq 8). Probe `open` + `first_attempt_ms` persist; the clamp lift is re-applied in `__init__`.

After restart the live book **allows a 6th** (and up to 8th) if canary / daily / LIVE_OK / A3 / wallet floor all pass. That is the incident fix, not a side effect. Do not restart `mal-floor-lane-host` (24 h entry block). Do not reset `first_send_ms`. 1108-1 clock reset remains unapproved.

## Assumptions this GO needs

1. Restart target is `mal-floor-lane.service` only (`python -m tools.floor_lane_executor`), same config JSON, LIVE_OK, bot_id, `end_ms` 1796688000000, stake/budget/daily/max_open.
2. Fill rows carry `sol_out` (h5_lane_executor). Unfilled `send_intent` has `sol_out` 0. Parent: 0 pending, so no unfilled slot at the restart instant.
3. `first_send_ms` stays **1794148248897**. Cursor/seq/prev/`start_seen` untouched.
4. Parent inventory (5 matching opens, 0 pending, 1 realized TP) still holds at the restart instant. This review did not SSH.
5. Helm applies the existing pin/restart path. **This agent is not authorized to deploy or to revise the pin.**

## NO-GO cases (not this patch)

- Resetting `first_send_ms` or restarting the host as part of this restart.
- Expecting e879’s double-count to keep refusing the live book.
- Raising `LANE_BUDGET_MAX_LAMPORTS` or `CANARY_MAX_MS`.

## Tests

```
E879_ROOT=/tmp/mal-e879-live python3 -m pytest ARTIFACTS/lab/floor-live-f68cf56f/test_floor_live_f68c_presend.py -q
# 24 passed

python3 -m pytest ARTIFACTS/lab/floor-live-budget-3710e1af/test_floor_live_budget_safety.py \
  ARTIFACTS/lab/floor-live-budget-3710e1af/test_floor_live_init_cutover.py -q
# 29 passed, 2 failed: 3710 blob-pin tests (executor; DEC-035 Am.3 would also mismatch)

python3 -m pytest ARTIFACTS/lab/floor-live-e8790f93/test_floor_live_e879_clamps.py -q
# 22 passed, 1 failed: e879 blob-pin (behavioral clamp/canary/0.30 tests still pass at f68c)

python3 -m pytest tools/test_floor_lane_executor.py -q
# 77 passed (in-tree; not this proof)
```

Logs: `pytest.out`, `pytest-prior31-at-f68c.out`, `pytest-e879-clamps-at-f68c.out`, `pytest-in-tree-floor.out`.
