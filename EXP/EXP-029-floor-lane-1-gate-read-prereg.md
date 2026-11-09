# EXP-029: floor-lane-1 real-fill gate read (DEC-035 §6 registration)

**Pre-registration.** This file registers when it merges to main. The start instant S below must still be in the future at that merge. If it is not, this file does not register, and a new PR with a later S is needed. Revision 3 was written 2026-11-08 by manager21, after QP NOT OK and REVIEWER REQUEST_CHANGES at 5bc73087 and at ba9eb60a. Nothing has been sent live yet (true at Revision 3 only; see Erratum 1): at writing the lane has run dry only, with 0 entries attempted. No fill, P&L, price or exit of any lane trade existed or was read.

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

- **S = 2026-11-09T06:00:00Z.** The host start at 08:32:01Z blocks all FLOOR entries for about 24 h (runbook `scripts/mal-fast/README.md`), so no entry is possible before S. **This sentence is wrong; see Erratum 1.**
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

## Erratum 1 (2026-11-08, written before S; outcome-blind)

**What was wrong.** §2 says the host start at 08:32:01Z blocks all FLOOR entries for about 24 h, so no entry is possible before S.
- That is false. The runbook's 24 h block applies only to a *restart*, which records a `host_down` span. The 08:32:01Z start was the host's first start. Its checkpoint had `blocked_pools` 0, `gaps` 0 and `gap_refusals` 0 (MiScusi #2284, #2285).
- Helm started the live executor at 2026-11-08T14:19:43Z (HELM-REPLY-1107-2e). floor-lane-1's first live `send_intent` is at 2026-11-08T14:30:47.391Z (MiScusi #2289; HOLDOUT_LEDGER first-send record, PR #1288). So floor-lane-1 sends real trades **before S**.

**Unchanged.** S stays 2026-11-09T06:00:00Z. The trade definition, C, R, the tests, the deadline and the no-second-look rule are unchanged. A `send_intent` with `ts_ms` < S is never a trade of this read, and its P&L never enters the four tests. This holds even when its position is still open at S or exits after S.

**Pre-S rows: how they count.** These rules are fixed at 2026-11-08T14:35Z (`date -u`), before S and before any floor-lane-1 exit. The manager has read only the first buy's cost basis and the lane headroom (MiScusi #2288, 0.031673840 SOL, one open position); no exit, proceeds or mark existed.
1. **FAIL ends (a) and (b) apply from the live start, 2026-11-08T14:19:43Z, not only from S.** The lane budget, daily stop and open cap are shared state, and pre-S floor-lane-1 losses count toward them like another lane bot's losses (§3 "Other lane bots").
   - If `lane_budget_stop` fires, or condition (b) holds, at any instant from the live start onward, the read is FAIL at that event under §3. A FAIL before S still sets C to the first 00:00Z after the event and R = C + 25 h, with an empty or partial trade set.
   - The lane cannot escape a stop by restarting the clock (§3 "No second look").
2. **Open slots.** floor-lane-1 positions opened before S and still open at S hold lane slots, like another bot's positions. `lane_full` refusals of floor-lane-1 entries in [S, C) are reported in three groups: (i) another lane bot holding slots, (ii) only pre-S floor-lane-1 positions holding slots, (iii) neither.
3. **Pauses and stops before S** are judged by the same §3 rules from the live start. A stop not on the pause list, made by a person between the live start and S, is a FAIL end.
4. **Reporting** (§6). The read lists the pre-S floor-lane-1 sends separately: count, UTC days, and total P&L by the §2 sum, with positions still open at R scored as §2 says. They are disclosure only and are not in any test.

## Erratum 1a (written 2026-11-08T14:39:07Z by `date -u`, before S; QP MINOR 1–6 on PR #1289)

This section tightens Erratum 1. It changes no test or threshold, and it does not change S, C, R or the trade definition.
1. **(b) before S.** Between the live start and S, condition (b)'s clause "no `floor-lane-1` trade is sent" reads "no `floor-lane-1` `send_intent` row is written". From S onward, the §3 text applies as written.
2. **Every §3 end applies from the live start (2026-11-08T14:19:43Z)**, not only the stops. This includes "Rule changes are a FAIL end" and "Any change installed without a merged amendment is also a FAIL end".
3. **Successor bot_ids.** Erratum 1 items 1–4 and this section apply equally to any successor bot_id named under §3 (ii).
4. **`lane_full` groups** (this replaces Erratum 1 item 2's split; disclosure only). Each `lane_full` refusal of a floor-lane-1 entry in [S, C) falls in one group:
   - (i) at least one slot is held by another lane bot;
   - (ii) no other lane bot holds a slot, and at least one slot is held by a pre-S floor-lane-1 (or successor) position;
   - (iii) only in-read floor-lane-1 positions hold slots.
5. **What was read before Erratum 1.** The figure 0.031673840 SOL in Erratum 1 is the first buy's **cost basis**, which is entry side. Erratum 1 was exit-blind, not fully outcome-blind: one entry cost and the lane headroom were read, and no exit, proceeds or mark.
6. **Daily reports.** `lane_daily_report` books a position on its close day. From 2026-11-09, its daily figures mix pre-S and in-read floor-lane-1 positions. No daily figure is quoted as read-trade P&L. The read alone computes in-read P&L, by the §2 sum over trades with `ts_ms` ≥ S.

## Erratum 1b (Revision 2 written 2026-11-08T15:37:18Z by `date -u`, before S; the frozen 14-day entry window)

**Found.** The frozen executor refuses every new entry with `canary_expired` once `now > first_send_ms + CANARY_MAX_MS`. `CANARY_MAX_MS` = 14 × 86,400,000 (`tools/floor_arena_executor.py:65` and `:205-207`, Appendix A blob `b05d4c92`). `floor_lane_executor.entry_refusal` calls it unchanged (`tools/floor_lane_executor.py:414-419`).
- `first_send_ms` is the only use of that field. It is written once, on the first buy attempt, when it is null (`:211-214`). It lives in the executor's sidecar state file `floor-state-live.json` (`:134`).
- floor-lane-1's `first_send_ms` is 1794148248897 = 2026-11-08T14:30:48.897Z (HELM-REPLY-1107-2f). So entries end after **2026-11-22T14:30:48.897Z**, which is S + 13 d 8 h 30 m 48.897 s. The deadline is S + 21 d. Exits are not affected.
- Neither DEC-035 nor this file sets a 14-day limit.

**Rule, fixed now, before S, and blind to every exit, proceeds and mark.** The manager has read only entry-side facts of the two pre-S buys.
1. **Pre-committed window reset (no code change, no new bot_id).** Helm performs the following, whatever the read's interim results. The only exceptions are that C has already come or a §3 FAIL end has already occurred.
   - **When:** no earlier than **2026-11-16T06:00:00Z** and before **2026-11-22T14:30:48.897Z**. The planning target is no later than 2026-11-21T00:00:00Z, to leave margin.
   - **What:**
     - (i) Stop `mal-floor-lane.service` (executor only, never the host).
     - (ii) In `/var/lib/mal-live/floor-lane/state/floor-state-live.json`, set `first_send_ms` to `null` and change nothing else. Record the file's sha256 before and after, and the one-field diff.
     - (iii) Start the same unit again, at the same Appendix A blobs, §1 parameters, config, bot_id and LIVE_OK.
   - The next buy attempt then writes a new `first_send_ms` at or after the reset. The window then runs to at least 2026-11-30T06:00:00Z, the deadline.
   - **Allowed state edit.** This one-field state edit is registered here. It is not an unamended change (Erratum 1a item 2), and it changes no code.
   - **Pause.** The stop-to-start interval is a §3 restart pause: same blobs and parameters. Its instants are recorded as §3 requires.
   - **Positions.** The bridge state, the open positions and their exits stay with floor-lane-1 unchanged. No position is handed to another bot_id.
2. **Classification.** The reset is a capacity-limit change of the same kind as `max_open`, the budget and the daily stop. It is not a change to the l1 rule, its pins, the exits or the stake. It alters decisions only by turning `canary_expired` refusals after 2026-11-22T14:30:48.897Z into ordinary entry checks. No code-change route (successor bot_id, edit of `CANARY_MAX_MS`) is used for this window.
3. **"Done in time" is defined on state, not on sends.** The reset counts as done if both of these hold:
   - the executor's journal shows a live-mode start after step (ii), before 2026-11-22T14:30:48.897Z, with `first_send_ms` null or ≥ the reset instant;
   - at 2026-11-22T14:30:48.897Z the executor is running or in a §3 pause.

   After a done reset, later executor outages are judged by the existing §3 rules.
4. **Not done in time.** If C has not come and no §3 FAIL end has occurred, the read is **FAIL at 2026-11-22T14:30:48.897Z**. This is a new FAIL end, registered before S, of the same kind as §3 "Any other stop before C". Letting the window lapse cannot turn the read into NOT_DECIDABLE. If C comes first, the window has no effect on the read.
5. **`canary_expired` and (b).** `canary_expired` refusals never start or extend a §3 (b) 24 h window. (b) is judged on ledger state only.
6. **No throttling after S.** From S, a §3 (i) amendment may not lower `max_open` or `daily_loss_lamports`. If one is installed and C has not come by the deadline, the result is FAIL, not NOT_DECIDABLE.
7. **Reporting.** The read reports:
   - the reset's stop and start instants, the sha256 values and the field diff;
   - the number of floor-lane-1 positions open at the stop;
   - the `canary_expired` refusal counts.

## Erratum 1c (written 2026-11-09T01:34:19Z by `date -u`, before S; hidden probe-port limits)

**Found.** The live port wraps the probe executor: `FloorLanePort` → `B.make_live_port` → `BridgeExecutor(probe_live.LiveExecutor)` (`tools/arena_live_bridge.py:1328-1603`, Appendix A blob `06ec1813`). The bridge builds it from a config with `"max_open": cfg.max_open, "max_attempts": 60` and no `limits_profile` (`:1514`). So `probe_executor.Limits` takes the `dec019` profile (Appendix A blob `07b2851b`, `:138-150`), and `Limits.__post_init__` clamps every limit to that profile's maxima. In live mode, `probe_live.LiveExecutor.handle_signal` (blob `15de5829`, `:438-451`) refuses a buy with `limit:<why>` from `pe.check_buy`, whose live-mode soft stops apply:
- **`max_open` 3**, not the §1 value 8. This is already binding. The first refusal came at 2026-11-09T01:16:26.005Z: a `send_intent` row (`intent:6c99e6047ef2d6a6`), then a `writeoff` with `reason` `not_sent` 7 ms later, with 3 floor-lane-1 positions open (MiScusi #2310, #2311). By §2 it is not a trade.
- **`max_attempts` 60.** After 60 probe buy attempts, no new buy is ever sent. At writing, the count is 3 (`state/probe/state-live.json`, #2311).
- **`max_days` 7**, counted from the probe's `first_attempt_ms` 1794148247392 = 2026-11-08T14:30:47.392Z. From **2026-11-15T14:30:47.392Z**, no new buy is ever sent.
- `loss_cap_lamports` (= `budget_lamports`, realized only) and `end_ms` (the bridge's own) do not bind before the lane's own stops. They are unchanged.

Neither DEC-035 nor this file sets these three limits. Without a fix, n ≥ 100 is impossible: there would be at most 60 attempts, and none after 11-15T14:30:47.392Z.

**Outcome-blindness.** The manager has read only entry-side facts of floor-lane-1's three live buys: instants, cost basis, the lane headroom, and the refusal above. No live exit, proceeds or mark exists or was read. One disclosed contact: the host decision log shows one `exit` row, tp_1.5x, for the dry-run entry seq 3 (order `503c632a6f8f399a`, never on-chain), read at 2026-11-08T21:3xZ while checking that no live sell was missed (#2303). No P&L of it was read.

**Rule, fixed now, before S.**
1. **Pre-committed execution fix, same bot_id.**
   - **The change:** PR #1297 at head `99afddd8f93fca6775d05b3f039471f256ebd85b`. It touches only `tools/floor_lane_executor.py`, new blob `6f353716e29f72c8f92699310d44f21ce23dd0dd`, plus its test file. In `FloorLanePort.__init__`, it sets `ex.limits.max_open` = the lane config's `max_open` (8), `max_attempts` = 100,000 and `max_days` = 60.0, next to the existing `priority_lamports` override. No other Appendix A blob changes.
   - **The install:** Helm stops `mal-floor-lane.service` (the executor only, never the host). Helm starts it again from a clean checkout of a main sha whose blobs equal Appendix A, except `tools/floor_lane_executor.py` = `6f353716…`. Same config, §1 parameters, bot_id `floor-lane-1`, state files and LIVE_OK.
   - The stop-to-start interval is a §3 restart pause. Its instants, the old and new pins and the blob check are recorded as §3 requires.
   - Positions, bridge state and the lane ledger carry over unchanged. No position is handed to another bot_id.
2. **Classification.** This is a §3 (i)-kind lane-limit correction, not a rule change.
   - It makes the installed open cap equal the registered §1 `max_open` 8. It removes two stops that no document sets.
   - It changes no decision of the host (which does not import `tools/floor_lane_executor.py`). It changes no dry row: `FloorLanePort` is built only by `build_live_port` on `run --live`, and the dry path uses `B.DryPort`. This is why the §3 (ii) md5 replay and the §3 (ii) successor bot_id do not apply to this one change. DEC-035 Amendment 2 records the bot_id ruling.
   - The change only turns `limit:max_open`, `limit:max_attempts` and `limit:max_days` refusals into ordinary entry checks.
3. **When.** As soon as PR #1297 merges with QP OK and REVIEWER APPROVE at its exact head. In every case it must be before **2026-11-15T14:30:47.392Z**, and before the probe attempt count reaches 60.
   - **Done** means the executor's journal shows a live-mode start at the new pin, and the blob check passes, before both of those limits.
   - **Not done in time:** if C has not come and no §3 FAIL end has occurred, the read is **FAIL** at the earlier of the two instants. This is a new FAIL end of the same kind as Erratum 1b item 4. A lapse can never turn the read into NOT_DECIDABLE.
4. **Refusals before the install.** `limit:max_open`, `limit:max_attempts` and `limit:max_days` rows are `send_intent` + `writeoff` `not_sent` pairs. They are therefore not trades (§2), and they are pauses (§3). They never start or extend a §3 (b) window. The read reports their counts by label and by [live start, S) and [S, install).
5. **Unchanged.** S, the trade definition, C, R, the four tests, the deadline, FAIL ends (a) and (b), Erratum 1b and its window reset. The reset leaves the probe state alone: `max_days` 60 counts from 2026-11-08T14:30:47.392Z and runs past the deadline.
6. **Reporting.** The read reports the install instants, the pins, the blob check, and the refusal counts of item 4.

## Erratum 1d (written 2026-11-09T01:42:06Z by `date -u`, before S; probe `sell_stuck` halt, bridge latches, and QP/reviewer MINORs on PR #1298; revised after QP NOT OK at fb9400ee)

**Found (QP MAJOR on #1298).** The probe executor has one more buy stop that this file did not classify. `probe_live.LiveExecutor.handle_signal` refuses a buy with `limit:sell_stuck` while `sell_stuck()` holds (`tools/probe_live.py:423-424`, `:445-446`, Appendix A blob `15de5829`), that is, while any open position is `stuck` or `abandoned`.
- A position becomes `stuck` after `sell_retries` failed sells (default 5, `:364`, `:721-723`). It keeps retrying, with backoff from 30 s up to 10 min (`:784-785`). The probe's own flag clears when the position sells.
- A position becomes `abandoned` at `sell_max_attempts` (at most 10, `:55`, `:365`, `:725`). It then has no more sells, and buys stay stopped until someone edits the state.

**Found (QP NOT OK on #1299 at fb9400ee): bridge latches.** On this stack the halt does **not** clear when the stuck position sells.
- When a sell lands with an error and the position becomes stuck, the bridge sets the latch `stuck_sell` (`tools/arena_live_bridge.py:1162-1163`, `:1464-1471`, blob `06ec1813`). No code clears it.
- Every latch stops new buys with `latched:<name>` *before* any `send_intent` row is written (`tools/arena_live_bridge.py:718-720`; `tools/floor_lane_executor.py:444`). A latch is set once, with a `latch` row (`latch`, `why`, `ts_ms`) in the bridge log `bridge-log-live.jsonl` and an ALERT line in the journal (`tools/arena_live_bridge.py:455-460`).
- Other latches on this stack also stop buys for good. They include `chain_break`, `sealed_path`, `frozen_sha_mismatch`, `stake_mismatch`, `landed_buy_unreadable`, `parity_hard_halt`, `read_record`, `twin_pin_mismatch`, `tieout_mismatch`, `a3_pins_changed`, `owner_line`, `ata_rent_not_refunded` and `lane_buy_error` (`tools/floor_lane_executor.py:65-66`, `:270`, `:523`). They also include the loss-cap and port-error latches the bridge sets.
- Only `feed_break` clears itself (`tools/arena_live_bridge.py:865`, `:887`), when the feed recovers.

PR #1297 does not change this. At writing, no floor-lane-1 position has a failed sell (MiScusi #2310: `buy_sent` 3, `buy_landed` 3, no sell rows).

**Outcome-blindness.** The same as Erratum 1c. No live exit, proceeds or mark exists or was read.

**Rule, fixed now, before S.**
1. **Refusals.**
   - `limit:sell_stuck` refusals are `send_intent` + `writeoff` `not_sent` pairs (the same port path as Erratum 1c item 4).
   - `latched:<name>` refusals write no `send_intent` at all.

   Neither kind is a trade (§2). While they last, they are a §3 pause, and they never start or extend a §3 (b) window.
   - **`limit:sell_stuck` halt time** runs from the probe's `sell_stuck` alert for a position until no position is stuck or abandoned. It comes from the probe fill log and the journal.
   - **Latch halt time** runs from the latch's `latch` row to its removal, or to the deadline.

   The read reports the counts and halt times by label: `limit:sell_stuck` split into stuck and abandoned, and each latch by name.
2. **No escape into NOT_DECIDABLE.** The read is **FAIL** at the deadline, not NOT_DECIDABLE, if C has not come by 2026-11-30T06:00:00Z and, within [S, deadline), the total time new buys were halted by either or both of the following is 24 h or more:
   - `limit:sell_stuck`, stuck or abandoned;
   - any latch other than `feed_break`.

   Overlapping halts count once. `feed_break` stays the §3 "host or feed failure" pause, judged as before.

   Rationale: these halts come from the book's own execution path (sell failures, chain or ledger integrity, pins, stake, fee/rent), and DEC-035 §6 counts real execution failures. The positions' P&L is scored as §2 says.
3. **No remedy is pre-authorised. Removing a latch is a state edit.** The following are state edits:
   - clearing a probe `stuck` or `abandoned` flag;
   - removing any bridge latch (including `stuck_sell`), by hand or by any tool;
   - a manual sell or sweep of a lane position;
   - any other edit of executor state for these.

   §3 allows only two kinds of change, (i) lane limits and (ii) execution-only code, and Erratum 1b adds one named state edit. None of these covers the edits above. So a state edit is allowed only after a later amendment adds it as a named change, merged before it is done. Otherwise it is a FAIL end at its instant (§3, "installed without a merged amendment"). Helm's wallet-wide STOP/HALT stays a pause, as §3 lists.

**Clarifications of Erratum 1c (QP and reviewer MINORs on #1298).**
4. **`end_ms`.** The installed live config `/etc/mal-floor-lane/live-3710e1af8cf434faca570046de896a7702464709.json` has `end_ms` 1796688000000 = 2026-12-08T00:00:00Z (MiScusi #2312). That is after the deadline, so it binds nothing in this read. The new pin keeps the same value; a lower value would be an unamended change.
5. **Merge order.** Both #1297 (main 54051438) and #1298 (main e8790f93) merged at 2026-11-09T01:41Z, before any install. Erratum 1c item 3 is read as requiring both merges before the install, and both happened.
6. **"Attempt 60".** The source of the attempt count is the `attempts` field of `<state_dir>/probe/state-live.json` (`/var/lib/mal-live/floor-lane/state/probe/state-live.json`). At writing it is 3 (#2311). The install record reports its value at the stop.
7. **Scope.** `tools/forge/dsl_lane_executor.py` builds its live port through the same `FloorLanePort`, so the #1297 fix applies to any DSL lane bot as well. No DSL bot is registered or running.
   - Erratum 1c item 2's "`FloorLanePort` is built only by `build_live_port`" is true for floor-lane-1's executor. For DSL bots the port is built in the DSL executor's own live path, never on a dry path.
   - DEC-035 Amendment 2's "it only makes §2 item 3 take effect" is too strong. Read it as: "it makes §2 item 3 take effect and removes two probe stops (60 attempts, 7 days) that this DEC does not set."

## Erratum 1e (written 2026-11-09T13:48:09Z by `date -u`, after S; the budget pre-send double count)

**Written after S.** S was 2026-11-09T06:00:00Z. This erratum changes no trade definition, no test, no C or R rule and no deadline. It corrects one execution check and the §3 (b) formula that mirrors it.

**Found.** The executor's budget pre-send (`tools/floor_lane_executor.py`, Appendix A as amended by Erratum 1c, blob `6f353716`, `evaluate`) refuses when `capped_pnl - (open_stakes + stake + reserve) <= -budget`. `capped_pnl` is lane cash plus min(fresh mark, stake) per filled open position, and lane cash already holds every filled buy's SOL out (`tools/h5_lane_executor.py` `lane_fold`). Each filled position whose mark is below its stake is therefore charged twice. From 2026-11-09T10:46:27Z to 12:33:53Z, 16 host entries were refused `lane_budget_presend` (MiScusi #2344; host log seq 13–30) with lane cash -139,591,048 lamports and 5 filled positions open. Losing all 5 and the new one in full ends near -192,750,000 lamports, inside the -300,000,000 budget. DEC-035 Amendment 3 records the limit's intended reading.

The §3 (b) formula, `cash - (open stakes of all lane bots + 30000000 + fee_reserve_lamports x (open positions + 1)) <= -300000000`, carries the same double count. Its stated premise, "When this holds, the pre-send budget check refuses any new floor-lane-1 entry", is not exact even for the code before the fix (there, fresh marks up to stake add headroom that the (b) formula leaves out), and after the fix it is false.

**Outcome-blindness.** The manager has read, since S: the lane report lines (sends, landed, failed, exits, realized, open, basis, headroom; #2332–#2343), the first exit (order `15b6196fab12c71c`, tp_1.5x, landed 2026-11-09T05:33:08.820Z, realized +0.018778152 SOL; entered before S, not a trade of this read), the host log rows' seq, kind, instant, order_id and exit reason (#2339, #2344), and the probe state's entry marks (#2345: entry-time marks only, no current mark). No current mark or unrealised value of any open position was read directly. **Disclosure (QP MAJOR 2):** the 16 refusals themselves carry mark information. Under the old check they can only happen if the sum over the 5 open floor-lane-1 positions of min(fresh mark, stake) was at most 42,751,048 lamports at each refusal instant (cash −139,591,048 − (150,000,000 + 30,000,000 + 6 × 3,860,000) + value ≤ −300,000,000), that is, the 5 positions together marked at least about 71.5% below their 150,000,000 lamports of stake, or their marks stale. The manager knew this when choosing the fix. The fix itself follows from the code and DEC-035 §2 item 1's own sentence.

**Disclosure (QP MAJOR 1): this erratum removes a FAIL path that is under way.** Under the registered (b) formula, the condition has held since the fifth position filled at 2026-11-09T06:06:52Z: cash −139,591,048 − (150,000,000 + 30,000,000 + 6 × 3,860,000) = −342,751,048 ≤ −300,000,000. No floor-lane-1 trade has been sent since the 06:06:50Z send. The old check keeps refusing while the marks stay that low, so (b) could end the read as FAIL no earlier than 2026-11-10T06:06:52Z, unless exits before then return enough cash to break the condition (the 24 h exits of the 11-08 positions fall from about 14:30Z on 11-09). Item 4 switches the formula at install, and that stops this FAIL path. The manager's case: (b) is titled "budget exhaustion", and the ledger is not exhausted (a full wipe of all 5 open positions plus a new entry ends near −192,751,048 lamports), so the old formula's FAIL would score a counting error, not a budget event. QP rules on this disclosure.

**Selection under the old check (QP MINOR).** In [S, install) the old check made floor-lane-1's entries depend on its open positions' marks: entries were refused while those positions were marked down. The read reports the refused host entries of that interval by count, and states that the set of trades in [S, install) was filtered this way.

**Rule.**
1. **The fix, same bot_id.** PR #1303 at head `07ef13d4dbc9eb1902bf4baeac61429663b75f5e` changes only `tools/floor_lane_executor.py` (new blob `556d15623459d56c8db706e2a2a5b2b2257cd577`) and its test file. If review changes the code, the merge record of this erratum names the final head and blob. The pre-send becomes `wipe_floor - (stake + reserve) <= -budget`, where `wipe_floor` = lane cash minus the stakes of open positions whose buy is not yet filled (any lane bot). Marks never add headroom. Every other check in `evaluate` is unchanged and in the same order.
2. **Classification.** A §3 (i)-kind lane-limit correction, as Erratum 1c item 2: the host does not import this file, and the dry path never builds the lane ledger, so no host decision and no dry row changes. The §3 (ii) md5 replay and successor bot_id do not apply. DEC-035 Amendment 3 records the bot_id ruling.
3. **Install.** As Erratum 1c item 1: Helm stops `mal-floor-lane.service` only (never the host), starts it from a clean checkout of a main sha whose Appendix A blobs are unchanged except `tools/floor_lane_executor.py` = `556d1562…` (or the final blob so recorded), with the same config, §1 parameters, bot_id, state files and LIVE_OK. The interval is a §3 restart pause, recorded as §3 requires. The **install instant** is the executor's journal line of its live-mode start at the new pin, with the blob check passing (as Erratum 1c item 3 "Done"). No install deadline: until the install, the old check runs.
4. **§3 (b), corrected from the install instant.** From the install instant, (b)'s condition reads: `cash - (stakes of open positions of all lane bots whose buy is not yet filled + 30000000 + fee_reserve_lamports x (open positions of all lane bots + 1)) <= -300000000`. Before it, the condition as written applies. A (b) window that spans the install must hold under each formula for its own part.
5. **Refusals.** `lane_budget_presend` refusals send nothing, so they are not trades (§2). They are not a FAIL end except through (b). The read reports their count in [S, install) and [install, R).
6. **Unchanged.** S, the trade definition, C, R, the four tests, the deadline, FAIL end (a), Errata 1–1d and the window reset of Erratum 1b.

## Erratum 1f (written 2026-11-09T16:33:01Z by `date -u`, after S; wallet-floor refusals and a wallet top-up)

**Found.** The FLOOR executor's wallet floor (`tools/floor_arena_executor.py` `floor_problem`, Appendix A, unchanged) refuses a buy with `wallet_floor` when balance < held open stakes + stake + send fee + rent reserve + (open + 1) × sell reserve + floor. The balance has already paid for the held stakes, so they are counted a second time, as in the bridge's own floor (`tools/arena_live_bridge.py` module docstring: "a stress test, not an accounting identity"). The floor is max(0.05 SOL, funded − 0.25 SOL, configured) = 750,000,000 lamports, with the config's `funded_lamports` 1,000,000,000 and `wallet_floor_lamports` 250,000,000 (`wallet_floor_lamports`, same file). At 2026-11-09T15:35:25Z one host entry was refused `wallet_floor` with 6 positions open (MiScusi #2358). The public balance at 16:31:58Z was 933,909,761 lamports (#2359). At that balance the floor allows about 5 open positions, and each 30,000,000 lamports of realized loss removes about one more.

**Outcome-blindness.** No mark or unrealised value was read. The balance is public and moves with sends, fills and exits already reported.

**Rule.**
1. **Refusals.** `wallet_floor` refusals send nothing. They are not trades (§2) and not a FAIL end. They are a pause of the same kind as `lane_full`. The read reports their count in [S, R).
2. **Top-up.** If Helm adds SOL to the floor-lane-1 wallet on the owner's word, with no change to code, config (`funded_lamports` stays 1,000,000,000), limits or state files, that is a §3 (i)-kind capacity change. It changes no host decision and no rule. It only turns `wallet_floor` refusals into ordinary entry checks. No executor restart is needed. The read continues with the same S and every trade since S. The top-up instant is the landed transfer's block time, from Helm's reply. The read reports the instant, the amount, and the `wallet_floor` refusal counts before and after it.
3. **Unchanged.** The 0.30 SOL lane budget and its STOP, the daily stop, max_open 8, the stake, S, the trade definition, C, R, the four tests, the deadline, and Errata 1–1e.

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
