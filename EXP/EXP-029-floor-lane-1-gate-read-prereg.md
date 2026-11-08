# EXP-029: floor-lane-1 real-fill gate read (DEC-035 §6 registration)

**Pre-registration.** This file registers when it merges to main. The start instant S below must still be in the future at that merge. If it is not, this file does not register, and a new PR with a later S is needed. Revision 3 was written 2026-11-08 by manager21, after QP NOT OK and REVIEWER REQUEST_CHANGES at 5bc73087 and at ba9eb60a. Nothing has been sent live yet: at writing the lane has run dry only, with 0 entries attempted. No fill, P&L, price or exit of any lane trade existed or was read.

Nothing in this file says or implies that FLOOR makes money. The read below is the only route by which lane trades at 0.03 SOL can count toward the promotion gate (CLAUDE.md; DEC-035 §6). A PASS is necessary for any stake above 0.03 SOL; it is never sufficient.

| Field | Value |
| --- | --- |
| **ID** | `EXP-029-floor-lane-1-gate-read-prereg` |
| **Rules** | [DEC-035](../DEC/DEC-035-live-learning-lane.md) §2 (with Amendment 1), §5, §6, §7; CLAUDE.md "Promotion gate". No gate threshold changes. |
| **Owner basis** | Owner in chat to manager21, 2026-11-08 ~00Z (MiScusi notebook n_j0z3_wYlJOMCaw): "Let's get started on Floor, you can ramp it up as you see it working ... just skip the forward testing and go to live." The ramp (0.03 → 0.10 → 0.25 SOL) moves on evidence. DEC-035 §6 says what that evidence is: this read plus the owner's word at each step. |
| **Hypothesis** | Live `floor-lane-1` at 0.03 SOL per position has a mean realized SOL per trade > 0 after all costs, on real fills. |
| **Expected outcome** | Unknown. At writing there are 0 real-fill FLOOR trades. Earlier live lane books (the H5 lanes) lost money: lane total about −0.33 SOL (manager21 strategic review, 2026-11-07). The prior is not favourable. |

## 1. Book (frozen)

- **bot_id** `floor-lane-1` (`tools/floor_lane_executor.py:44`, `LANE_BOT_ID`). It decides on the book arm `l1-floor-v2`: FLOOR-OPTION v2, rule `tools/arena_rule_l1.py`, rule pins sha `5e12f8f597571d6d5191a7ba38125c67ad723b7d`, as in the docs/HOLDOUT_LEDGER.md entry of 2026-11-08 made under DEC-035 §7 (PR #1275).
- **Frozen sha:** main `3710e1af8cf434faca570046de896a7702464709` (the PR #1279 merge).
- **Frozen code:** the full set of `tools/` files that the executor (`tools/floor_lane_executor.py`) or the decision host (`tools/floor_lane_host.py`) import, directly or indirectly, including imports inside functions. That is 38 files, listed with their git blob ids at 3710e1af in Appendix A. The executor and the host run from clean checkouts of any main sha whose blobs for all 38 files equal Appendix A.
- **Decision host:** `mal-floor-lane-host.service`, started 2026-11-08T08:32:01Z from main e89f15364c85. `git diff e89f15364c85 3710e1af` over the 38 files touches only `tools/floor_lane_executor.py`, which the host does not import. So the host runs the Appendix A blobs. The read reports the host start row's frozen sha and host file sha256.
- **Lane parameters** (executor config; DEC-035 §2 and Amendment 1):
  - `stake_lamports` 30000000 (0.03 SOL);
  - `max_open` 8;
  - `budget_lamports` 300000000 (−0.30 SOL lifetime, cost-capped pre-send);
  - `daily_loss_lamports` 100000000 (−0.10 SOL daily, a pre-entry check).

  Exits are the rule's own: tp at 1.5x, or 24 h plus the 1.9 s backstop.

## 2. Start instant, trade unit, P&L

- **S = 2026-11-09T06:00:00Z.** The host start at 08:32:01Z blocks all FLOOR entries for about 24 h (runbook `scripts/mal-fast/README.md`), so no entry is possible before S.
- **Trade.** A trade is a lane-ledger row with `ev` = `send_intent` and `bot_id` = `floor-lane-1`, whose `ts_ms` is ≥ S and < C (§3).
  - **Not trades:** a `send_intent` later closed by a `writeoff` row with `reason` = `not_sent`. Such an order never reached the chain and moved no SOL.
  - **Trades:** every other `send_intent`, including those closed by `writeoff` `buy_expired` and buys that landed with an error. Each counts at its real cash, which may be 0 or its fees alone. The read reports how many trades had 0 cash.
  - Dry rows (`would_send`, `"mode": "dry"`) are never trades.
- **Trade instant and day.** The instant is the `send_intent` row's `ts_ms`. The trade day is the UTC day of that instant.
- **Trade P&L** in lamports is the sum of (`sol_in_lamports` − `sol_out_lamports`) over every lane-ledger row with `bot_id` `floor-lane-1` and that trade's `mint`. The sum runs from the trade's `send_intent` row through the first row after it that closes the position: `exit`, `writeoff`, or a `fill` with `ok` not true (a buy that landed with an error; `lane_fold`, `tools/h5_lane_executor.py:181-185`). The sum includes `fill` rows and `fee` rows (failed or superseded sells). A later `send_intent` on the same mint starts a new trade.
  - In this ledger `sol_out_lamports` already contains the network, priority and tip fees. The fields `fee_network_lamports`, `fee_priority_lamports`, `fee_tip_lamports`, `rent_unreclaimed_lamports` and `rent_refunded_lamports` (`tools/h5_lane_executor.py:366-367`) split it out. They are not added again.
  - A trade still open at R gets the same sum with no proceeds: all cash out counts and nothing comes back.
  - Rows with `estimated` true count as recorded, and the read lists how many there are.
  - `snap` rows carry no trade and are ignored. Later sweeps are not credited.
- **No trade is dropped** (DEC-035 §6).

## 3. Read instant (one look, set by counts), ends, pauses, changes

- **C** is the first 00:00:00Z at or after S at which the trades with instant in [S, C) number at least 100 and fall on at least 5 distinct UTC trade days. C depends only on trade counts and days, never on any P&L.
- **R = C + 25 h.** By then every trade has passed its 24 h + 1.9 s backstop. The read runs once, at or after R, on the full set.
- **FAIL ends before C.** C becomes the first 00:00Z after the event, R = C + 25 h, and the result is **FAIL** whatever the four tests give, if either of these happens:
  - (a) the lane budget stop fires (`lane_budget_stop`: lane P&L ≤ −0.30 SOL, STOP file written by the executor);
  - (b) budget exhaustion without the STOP file. This is judged on the lane ledger's state, never on refusal labels: the base executor's refusal reasons are logged ahead of the lane's own (`tools/floor_lane_executor.py:413-418`).
    - Condition: for 24 h in a row, no `floor-lane-1` trade is sent, and at every instant in that window the lane ledger satisfies `cash − (open stakes of all lane bots + 30000000 + fee_reserve_lamports × (open positions of all lane bots + 1)) ≤ −300000000`.
    - `cash` is the sum of `sol_in_lamports − sol_out_lamports` over all lane-ledger rows. Open stakes and open positions come from the `lane_fold` view (`tools/h5_lane_executor.py`). `fee_reserve_lamports` is the executor config value.
    - When this holds, the pre-send budget check refuses any new `floor-lane-1` entry, whatever other refusal is logged.
    - The event instant is the end of the 24 h.

  Both apply even when the losses come from another lane bot (see "Other lane bots").
- **Pauses** do not end the read. Sends resume and stay in the read. The pauses are:
  - A3 pauses (`a3_pause:*`);
  - the daily stop and daily pre-send refusals;
  - `lane_full`;
  - gap holds;
  - restarts at the same Appendix A blobs and §1 parameters;
  - a wallet-wide STOP or HALT placed by Helm;
  - a host or feed failure, with its start and end instants taken from the watchdog (`/data/mal/hunt-1008/ops/watchdog/`) or the unit's journal.

  Each pause other than the automatic refusals is recorded with its instants in `/data/mal/hunt-1008/ops/manager21/state-manager21.md` (or a successor manager's state file) and in the MiScusi notebook before any lane P&L is quoted.
- **Any other stop before C is a FAIL end, at the stop instant.** This covers the manager or the owner stopping the lane, or a lane STOP file written by a person, for any reason not listed under pauses.
- **Changes before C.** Two kinds of change are allowed, and each only through a T1 amendment PR to this file. That PR carries QP OK and REVIEWER APPROVE at its exact head and merges before the change is installed. The read then continues with the same S, and every trade since S stays in it.
  - (i) **Lane limits:** `max_open`, `budget_lamports` or `daily_loss_lamports`, each within DEC-035. These are config only, with no code change.
  - (ii) **Execution-only code changes** to Appendix A files other than `tools/arena_rule_l1.py` and the rule pins. The amendment must carry an md5 decision-equivalence replay (CLAUDE.md operating notes): on a pinned replay window, the host decision log and the executor's dry intent and exit rows are byte-identical before and after the change.
    - DEC-035 §3 makes a code change a new bot_id. The amendment names the successor bot_id, and its trades join this set from its first send, under the same S, C rule and tests.
- **Rule changes are a FAIL end** at their install instant. This covers any change that alters decisions: `tools/arena_rule_l1.py`, rule pins sha `5e12f8f5…`, the exit rules, `stake_lamports`, or any change whose replay is not byte-identical. Any change installed without a merged amendment is also a FAIL end at its install instant.
- **Deadline.** If C has not come by 2026-11-30T06:00:00Z (S + 21 d), the read is **NOT_DECIDABLE** at that instant, by counts only.
- **No second look.** Any later registration of `floor-lane-1`, or of the `l1-floor-v2` rule under any lane bot_id, keeps S = 2026-11-09T06:00:00Z and includes every trade of this file's set. A NOT_DECIDABLE or FAIL under this file never restarts the clock.
- **Other lane bots.** The forge DSL lane executor (`tools/forge/dsl_lane_executor.py`, PR #1282) shares the lane ledger, lock, STOP file, budget, daily stop and open cap with `floor-lane-1`.
  - DSL bots may run before C. FAIL ends (a) and (b) apply whichever lane bot's losses cause them.
  - `lane_full` refusals of `floor-lane-1` entries are counted in the read, split into those with another bot holding slots and those without.
  - The H5 lane bots (`h5-lane-1`, `h5-lane-2`, `h5-lane-h`, `h5-lane-syn`) are stopped at writing.
  - The read lists every lane bot_id that sent in [S, R), with its trade count and total (DEC-035 §6).

## 4. Gate tests (CLAUDE.md; DEC-035 §6), on real fills only

All four tests run on the full trade set. Real-fill failures are observed, not modelled. The flat 15% and pressure-fail models apply only to simulated trades. No simulated trade enters this read, and real and simulated trades are never pooled.

1. n ≥ 100 trades.
2. At least 5 distinct UTC trade days, and strictly more than half of them have a day total > 0. A day total is the sum of the P&L of the trades whose trade day it is.
3. The lower 90% bound of mean SOL per trade is > 0, computed as follows:
   - put the n trade P&Ls in a numpy float64 array `x`, sorted by (`ts_ms`, `mint`);
   - `rng = numpy.random.default_rng(1)`;
   - `idx = rng.integers(0, n, size=(1000, n))`;
   - `means = x[idx].mean(axis=1)`;
   - the bound is `numpy.percentile(means, 5)` (default linear method).
4. The total is still > 0 after removing the 3 largest trade P&Ls.

**PASS** only if all four hold and no FAIL end occurred. Otherwise the result is **FAIL**, except under the deadline rule. Quant-proof rules on the read at its exact head.

## 5. What a PASS allows, and what it does not

- A PASS at 0.03 SOL does not show the edge holds at a larger stake (DEC-035 §6). Each scaling step (0.10, then 0.25 SOL) needs its own PR containing:
  - the stake;
  - a price-impact estimate at that stake on the pools of the PASS trade set;
  - the owner's word for that step in chat.

  That PR amends DEC-035 for `floor-lane-1`.
- A FAIL ends `floor-lane-1` at 0.03 SOL. There is no retune and no re-read of the same set. A changed rule is a new book, with a new bot_id and a new registration.

## 6. Reporting

- **Daily.** DEC-035 §5 runs `python -m tools.lane_daily_report --config <executor json> --day <UTC day>` (PR #1284). The output goes to the MiScusi notebook and `data/console.json`, losses included. The daily reports describe; they are not reads of this registration, and none of them triggers a decision. `lane_daily_report` books a position on the day it closes; this read books each trade on the day of its `send_intent`, so daily totals do not match the read's day totals.
- **The read output names:**
  - every lane bot_id that sent in [S, R), with its result;
  - the host start row's frozen sha and host file sha256;
  - the counts of `not_sent`, 0-cash, `estimated` and `lane_full` rows.
- **Seals.** The docs/HOLDOUT_LEDGER.md entry of 2026-11-08 (made under DEC-035 §7, PR #1275) covers the overlapped reads. Each of them is reported "not blind: live-lane observation declared <floor-lane-1 first-send instant>".

## Appendix A. Frozen files (git blob ids at 3710e1af)

This is the transitive closure of `tools.*` imports, including imports inside functions, from `tools/floor_lane_executor.py` and `tools/floor_lane_host.py`, computed with `ast` over `git show 3710e1af:<file>`. Every blob is the same on main 5a1c2300.

| File | Blob |
| --- | --- |
| `tools/arena_close_blocked.py` | `79eecbd202e48d84df5f3d1779ee27701114c905` |
| `tools/arena_declog.py` | `2baa782db6b93fe1dc225009176b889785a20763` |
| `tools/arena_flip_read.py` | `5fdefa28f184cb0d819a3fe18126246c72615397` |
| `tools/arena_live_bridge.py` | `06ec1813eb248c57b8eceeaa5166d5fcb28eade9` |
| `tools/arena_paper.py` | `5eef156bb617138a4c04fca6779f9ff4c5215b13` |
| `tools/arena_pool_table.py` | `daa22d3ab1a6c446c3ea44003ae9cdb6f53548cd` |
| `tools/arena_report_legs.py` | `7c192fd274da162f458eaae601b4c11532109f9a` |
| `tools/arena_rule_l1.py` | `cf4cf1c4b08476b96162f735ce9ec730625b402a` |
| `tools/arena_rules.py` | `a107a677b0d70d7363543478de4e681cb0e7c3bc` |
| `tools/arena_rules_v2.py` | `818d9be5cad74a90e5282a2927bbfffae1c365fd` |
| `tools/arena_score.py` | `f3adcd69c7ef62392eafc511b40d93a0dc31f13c` |
| `tools/arena_stack.py` | `092bbce0854dc6760706608cb706108f67d0179d` |
| `tools/arena_stack_v2.py` | `4832f9998d14f67118961c0c8dc302bffe12ed44` |
| `tools/arena_state.py` | `38e4c608115c99a52d202a6c82cd2a92c214879e` |
| `tools/arena_tieout_ledger.py` | `f8fa39f78982de9f4d01d9d1c370c0e9ee04f719` |
| `tools/arena_zst.py` | `a847d27ef1280e9f93ded5511a9579bf979aa32f` |
| `tools/cap_pick_oracle.py` | `64d5e49767c70379bfa5041dfc03976ead37d208` |
| `tools/floor_arena_executor.py` | `b05d4c92e1f91ec40f19e8d2ef4440b70d4e5d95` |
| `tools/floor_lane_executor.py` | `2b0b109a6d6232e4de0feb16339ce13ccd4cd22f` |
| `tools/floor_lane_host.py` | `a4387c24135f198f8b3ce7d75f810170fbace297` |
| `tools/h5_arena_executor.py` | `b96f023fe64eadd63a2d377e08a0d004a7038d20` |
| `tools/h5_arena_gates.py` | `fa4031e24516df5f2d86ba3b3817b519ec2ade7d` |
| `tools/h5_arena_ladder.py` | `c95e0c789530dd98e0c9af05034f3c672cbb04d3` |
| `tools/h5_executor.py` | `03995837317f65f62db2fb6b238ecaca956d01cb` |
| `tools/h5_lane_executor.py` | `c04b8e31362142cfe669b28b0506162d4bea990a` |
| `tools/h5_shadow.py` | `f23cf492211fd79287fa6ac82fd382f1f3e39a3d` |
| `tools/hv4_c0_parity.py` | `9c28929ea2b5fcd8b5ea6a4b62d0a9aeee34239d` |
| `tools/hv4_c0_tieout.py` | `aab4e63b5d65b1007bad7e140147f830a0f63cc9` |
| `tools/hv4_live_c0.py` | `25322a7f7dffcf02d0a33919eb7a8c6429da944d` |
| `tools/mint_account_fetch.py` | `6b9aad398a4231046cfafee44d21a008d65c7838` |
| `tools/paper_curve_math.py` | `42daf5adcdc1532cb29a6f5079997d25b10c86ea` |
| `tools/paper_price_path.py` | `8f551535acaa048996f110f4f30004d37b629b3b` |
| `tools/paper_tape_scoreboard.py` | `1955f45c4146e628ba0208cc3317a0f4f147aaf0` |
| `tools/probe_executor.py` | `07b2851b8086a7cd402574ad601af26a7344db84` |
| `tools/probe_live.py` | `15de58294fb80e2c3160a739c3bb5250a9b25442` |
| `tools/pump_structure_monitor.py` | `1ca0a88cecf0853d94336ea046ba1a910b79f198` |
| `tools/pumpswap_simulate.py` | `ec5f22a8048c100500bde49ca34622d88b6e6706` |
| `tools/pumpswap_tx.py` | `2c9f6dda26ddbed50a6231b47a85c91723a19d50` |
