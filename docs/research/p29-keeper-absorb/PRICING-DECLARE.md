# P29 PRICING-DECLARE v4: keeper stop-loss absorb. FROZEN BEFORE ANY PRICE PATH IS READ. NOT PRICED.

v1 was e0009fe6; this v2 applies all 15 required changes of the quant-proof ruling on #931 (/data/mal/hunt-1008/push-1016/hunt30/p29/QP-RULING-1025-p29-pricing.md, CHANGES at e0009fe6). A QP re-check on the exact new head is required before any price is read. Paper only, 0 Helius. Nothing here is an edge claim; no book has cleared the promotion gate. A met revive trigger is a count, not evidence of an edge.

Not read before this file: any price, quantity, reserve, exit, return or P&L of any tagged or untagged cluster. The only tape reads so far are RESULTS.md counts (signatures, block_time, venue, and the last tagged sell's reserve for the 2% bar) and the tag-coverage dry pass (section 9), which reads signatures, times and mints only.

## 0. Build route (QP item 7)
**v3 (QP re-check fixes 1-3).** P29 is a SEPARATE module, `tools/p29_keeper_rule.py` (class `P29KeeperRule`, the kit's rule API: `cells()` and `triggers(hour, state)`), plus `tools/p29_keeper_run.py`. They IMPORT the pinned kit functions (`Cell`, `ExitSpec`, `RunState`, `HourRows`, `post_price`, `Screen`, `legs`, `screen_cfg`, `VMap`, `iter_hours`, `check_vmap_paths`, `SEALED_HOURS`, `FORBIDDEN`) and copy none of them. The runner uses P29's own allowlist (exactly the section 4 blocks, paths and days; `tools/p29_keeper_coverage.BLOCKS`), NOT the kit's CLI guard, and still refuses every kit FORBIDDEN token except `exp011` (covered by the section 4 manager line), any path outside the allowlisted directories after realpath, any arena/meta-capture path and any hour >= 2026-10-02T10 (kit SEALED_HOURS). The kit file `tools/arena_screen.py` is not edited.

P29 therefore inherits (by import) the kit's constants and checks: the kit's constants and checks: D = 7 feed-delay slots, LAT_DECIDING = 1.9, STAKE_SOL = 0.25, send = 160,000 (DEC-031 Am.8 stack, `arena_rules.stack_config_v2`, passed explicitly), E1 floor (`arena_paper.E1_FLOOR_PER_005`), STRAND_RULES (total, sc), FAILS (flat, press), SUB_CELLS, the pressure intercept refit, worst-in-slot (tools/arena_report_legs.py, QP-approved #747, `REPORT_LEGS_PIN` 65f4d471...), MIN_POOL_AGE_S = 360, the V map (VMAP_DEFAULT) and the block guard. The cost and fail stack is pinned by file sha256 at main 6669c4cc7be1a76c51abc201374c1dab5178c632:

| File | sha256 |
| --- | --- |
| tools/arena_screen.py | b37282c7d23d3bfca310f2574b8bc2b9b0981e0e73ee2537e118f779244fbb57 |
| tools/arena_stack.py | a39b2ba68dd46b7d3a555fb8e06580718300f354020c1f91280ab7a0e0856778 |
| tools/arena_paper.py | 9d2fdb04a3da9bb38de9e30c91154f5a9ab2de513e6f4d7936c1eb1a54863514 |
| tools/arena_score.py | c4859323622794f5f97a131eb32256c905ca2ab6c1adf8ab21568fc2a98f27f3 |
| tools/arena_report_legs.py | 65f4d471e3ad19308a038c4d6563f22322134013f09c0afe2d64c648964a0d8b |
| tools/arena_stack_v2.py (`stack_config_v2`, send 160,000) | 95876c94d7e029e3959d8a3aed05ee64f1d461bc002ec62d035e74763587acb3 |
| tools/arena_rules.py (`stack_config`, `STACK_FIELDS`, `DEFAULT_FEED_DELAY_SLOTS`) | 048dd78901c52e0c14fe9b1611f354c49ac6d0bb37fad8e5be3e27c756e3e2af |

The kit modules that these import (`tools/arena_declog.py`, `tools/arena_state.py`) are reached through arena_rules and are not used by the screen path; the screen path's own imports are the five kit files above plus arena_stack_v2/arena_rules. The P29 modules themselves (`p29_keeper_rule.py`, `p29_keeper_run.py`, `p29_keeper_coverage.py`) are recorded by sha256 in the run output and the QP OK is given on their exact head. A test (`test_cost_stack_is_the_am8_stack`) asserts the screen config equals `stack_config_v2(7)` in every field except `rent`.

The runner records these hashes in its output and refuses to run if any differs. The kit's own guard is NOT changed (item 10): P29's data access is a separate tool-level allowlist of exactly the declared blocks and days (section 4); `tools/p29_keeper_coverage.py` already has it, the P29 runner copies the same list.

Venue scope (QP item 7): the arena cost stack and live path have no bonding-curve pricing. **Deciding cell = PumpSwap only.** Bonding-curve events are counted and report-only (priced only if a bonding pricing rule with fees and a pressure model is added by a later reviewed amendment; none is declared here). A PASS is therefore for the PumpSwap-only book, which is also what the arena/live path can trade. **Pool age >= 360 s** (kit MIN_POOL_AGE_S; the EXP-024 Look-2 seal until 2026-11-14T00Z) is applied to every entry: younger pools are skipped and counted.

## 1. Event (frozen)
- Tag: RESULTS.md rule (keeper fill signature from the keeper's public history), kind SL only; TP and `mixed` slots excluded (TP mirror is a separate family).
- Event: >= 3 DISTINCT tagged SL fill signatures with sell rows in ONE PumpSwap pool within 60 s (block_time); closes at the 3rd distinct signature; 60 s refractory per pool. The "last tagged sell" is the row (slot) of that 3rd distinct signature; later tagged rows before the entry are ignored for timing.
- Pool choice: a mint with several PumpSwap pools is traded in the pool that holds the event (the pool of the 3rd signature); if the 3 signatures span pools, the event is skipped and counted.
- Pre-drop price P_pre = V-priced price of the pool (`(quote_reserve + V) / base_reserve`, the kit's PumpSwap basis) at its last print at or before (first tagged sell of the event minus 60 s). **If there is no print in that look-back, skip and count.** P_last = V-priced price at the last tagged sell's post-sell state. Drop d = (P_pre - P_last)/P_pre. Skip if d < 3%. All of P_pre, P_last, the reversion target and the stop use the same V-priced basis (never raw vault price).
- V handling: a pool absent from the V map is skipped and counted ("missing V"). A pool whose map entry is a real 0 (or negative) V is used as that value. The two counts are reported separately.

## 2. Timing (QP item 1, 2, 3)
- Kit constants: D = 7 slots (feed delay, held separately from the 1.9 s send-to-land leg; the 1.9 s does NOT include the feed delay), LAT_DECIDING = 1.9 s. sps (seconds per slot) is the kit's per-hour clock `1 / slots_per_s` from the hour loader (tools/arena_screen.py `sps_of`), not a block_time difference.
- **T_tag = 1.0 s, declared, UNMEASURED.** Basis: tools/arena_feed.py is public logsSubscribe of the two pump programs decoded from logs only; it carries the signature but not the fee payer or signer and has no keeper subscription. Live tagging needs one added `logsSubscribe` with `mentions: [keeper]`, no getTransaction; T_tag covers pairing the keeper-mention notification with the pump log row and the create memo.
- **PRIMARY entry slot = last tagged slot + D + ceil((T_tag + 1.9) / sps)** = last tagged slot + 7 + ceil(2.9 / sps). Priced at the pool state at the landing slot (the kit's convention, one convention for the venue; QP item 4): the entry lands at that slot and takes that slot's reserves, with the kit's within-slot ordering, the V adapter, and the stack's fees, send 160,000, E1 floor and impact.
- **Exit slot = trigger slot + D + ceil(1.9 / sps)**, same landing-slot-state pricing (the tag is not needed on exits).
- **STRESS leg (report only, never decides, never a kill):** identical to the primary except T_tag = 3.0 s on the ENTRY (entry slot = last tagged slot + 7 + ceil(4.9 / sps)); exits as in the primary. A stress sign flip (mean <= 0) is stated in the headline of any result.
- **Best case (report only):** entry at last tagged slot + 2. Never used to decide.
- **Forward book (QP item 3):** the forward arena book measures, before its clock starts, the p90 over its live feed of (keeper-mention notification arrival minus pump-log arrival of the same signature) and uses T_tag(forward) = max(1.0 s, that measured p90). A measured p90 above 1.0 s makes this exploration result NON-TRANSFERABLE; the forward book is then a new cell with its own registration.

## 3. Trade (frozen)
- Entry: buy 0.25 SOL (STAKE_SOL) at the landing slot above. The entry is skipped and counted if the 2% impact bar fails on the landing-slot state, if the pool is younger than 360 s, if the pool has an open P29 position (overlap: new events while a position is open are skipped and counted), or if the kit's guard refuses.
- Reversion target R = P_last + 0.5 x (P_pre - P_last) (V-priced). Stop S = 0.90 x entry fill price, where the stop is measured on the V-priced pool spot (post-slot state) against the entry fill (average price), as the kit's stop exits are. Time cap: 120 s after the entry landing slot.
- **Edge cases (QP item 8), all frozen:**
  1. Entry-state price already >= R at the landing slot: skip, count ("target already met").
  2. Entry-state price already <= S-equivalent (spot <= 0.90 x the pre-entry spot): skip, count.
  3. Exit trigger = first slot after entry at which spot >= R (reversion), spot <= S (stop), or the cap; the EARLIEST PRINT that crosses either level decides (kit rule, tools/arena_screen.py `_price_exit`); a tp-hit and an sl-hit cannot be the same print. Exit slots that held both a tp-hit and an sl-hit print (at different prints of one slot) are counted as a report-only line. (v2/v3 text "the stop wins on a tie" is withdrawn.)
  4. No print after the trigger within the kit's GRACE_SLOTS: the position is STRANDED and treated per the kit's strand rules (sub-cells "total" and "sc"); both are read as separate sub-cells (section 5).
  5. Migration is not a case for a PumpSwap-only book (the pool exists already); a pool closed or drained during the hold is a stranded exit.
  6. A pool whose V changes mid-hold is repriced at the landing-slot V (kit behaviour).
  7. Overlap: see section 3 entry rule.
- No re-entry inside the refractory; one position per pool; no other parameter.

## 4. Days, paths, exp011 (QP items 9, 10)
All data are exploration-pool hours per docs/HOLDOUT_LEDGER.md (none is a holdout). Paths are under /data/mal/clean-view/.

| Block | Path | Ledger status | Role | Days |
| --- | --- | --- | --- | --- |
| fresh-0903 (getBlock walk) | /data/mal/clean-view/fresh-0903/w{1,2,3} | exploration pool, spent by EXP-012 | in-sample, report only | 09-04..09-08 |
| exp011-0909 (getBlock walk) | /data/mal/clean-view/exp011-0909/{b,c} | exploration pool since 2026-10-06 (four preconditions recorded) | OOS look | 09-10..09-14 |
| fast-pool-0918 (listener tape) | /data/mal/clean-view/fast-pool-2026-09-18T23_2026-09-22T00 | exploration pool | OOS look | 09-19..09-21 |
| oracle-insample-0922 (listener tape) | /data/mal/clean-view/oracle-insample-2026-09-22_25 | exploration pool ("seen" by the 972-cell grid) | OOS look | 09-22..09-24 |

Excluded and not read: oracle-live-0925 (void-window adjacency, partial days), the EXP-009 block [09-15T12, 09-19T01), fresh-0828 (reserved), explore-0814 (no keeper before 09-02), everything >= 2026-10-02T10 (D2/D3 sealed hours [10-02T10, 10-16T01) and all later hours), arena tape, meta-capture, HV4/Q50/DBC windows. The D2/D3 seals cover October hours only; September hours are outside them (confirmed against the ledger text and the kit's SEALED_HOURS).

**Manager line on exp011 (required by the QP item 10; to be recorded by the manager before the run):** the lab kit refuses any path containing "exp011" (tools/arena_screen.py FORBIDDEN) and hunt23 RULES.md says "Never open ... exp011-*". The manager records, as an explicit ruling, that those rules do not bind P29 because docs/HOLDOUT_LEDGER.md (the authority) lists exp011-0909 as exploration pool since 2026-10-06. The coordinator has recorded this as ALLOWED in the notebook. The P29 tool keeps its own explicit allowlist of exactly the blocks and days in the table; the kit's FORBIDDEN is not modified. If this line is not on record at run time, exp011-0909 is dropped from the OOS set (6 days remain, and the NOT_DECIDABLE rule below applies if < 5 days or < 100 events remain).

**Single OOS look:** the OOS days of the blocks that survive section 9 are read ONCE, pooled, after this file is merged and QP-ruled on its exact head. No interim peeks, no per-block reads. Days count only if they have >= 1 trade; >= 5 such days are needed.

## 5. Kill set (all frozen) (QP items 5, 6)
Evaluated on the primary leg, on ALL FOUR deciding sub-cells {flat 15%, pressure slope 1} x {strand total, strand sc}. The result is KILL if ANY of the following holds on ANY of the four sub-cells:
1. mean SOL per trade <= 0;
2. trade-level CI90 lower bound of the mean <= 0 (1,000 bootstrap draws, seed 1, 5th percentile);
3. **date-cluster CI90 lower bound <= 0** (resampling UTC dates, the kit's date-cluster resampler, seed 1, 1,000 draws);
4. total SOL <= 0 after removing the top 3 trades;
5. total SOL <= 0 after removing the best UTC day;
6. NOT a strict majority of the traded UTC days positive (exactly half is a KILL; days with >= 1 trade; and fewer than 5 traded days is not a kill but NOT_DECIDABLE);
7. fewer than 100 OOS trades (NOT_DECIDABLE, not a kill, when caused by coverage or count, see section 9);
8. median gross edge per trade (before fees and tx cost, fill to exit fill) <= 1%;
9. **worst-in-slot**: the lower mean (kit `end_minus_worst` / worst-in-slot leg, QP-approved #747) <= 0 on any of the four sub-cells is a KILL; if the worst-in-slot leg cannot be measured on the P29 rule, the result is **PROVISIONAL-BLOCKED**, not PASS.
PASS = none of 1-9 fires on any of the four sub-cells AND (Branch A of section 6) the placebo PASSES, or (Branch B) the placebo is report-only with the exact label of section 6, AND the dry-pass gates (section 9) hold. Stress, best-case, in-sample, bonding (report-only) and the placebo arms other than (ii) never rescue a kill.

## 6. Placebo (QP item 12)
Question: does the keeper tag carry information beyond "a sell burst"? Control events are untagged bursts: >= 3 sell rows by >= 3 distinct traders in one PumpSwap pool within 60 s, none tagged (any kind), same d >= 3% / 2% bar / 360 s age / V-band filters.

**Matcher v2 (FROZEN HERE, before the recount; the ONE recount; there is NO matcher v3).** v1 (drop +-2 pts, sellers +-1, no reuse) left 96.0% of OOS events unmatched (counts only, #1628). v2 is chosen on counts and pre-entry features only (no price after any event was read):
- Cross-pool only: a control is in a DIFFERENT pool, the same UTC day. The same-pool arm (type i) is report-only and is not run in the recount.
- A control is excluded when ANY tagged event of its own pool, filtered or not, lies within 600 s.
- Constraints: drop d within +-5 pts; burst duration within +-15 s; total sold SOL within +-25%; control distinct traders >= 3 (the variable is a floor, not a tolerance); same reserve decile (deciles over the pooled passing events and candidates).
- Each control is used at most 3 times.
- Deterministic greedy in (event time, pool, slot) order; score = |tot/etot - 1|/0.25 + |dur diff|/15 + |seller diff| + |dd|/0.05; ties by earliest control time, then smallest pool id, then smallest slot. No randomness.
- **Balance gate**: after matching, the standardised mean difference of the 5 match variables (total sold SOL, duration, distinct sellers, drop d, reserve decile), (mean tagged - mean control) / sqrt((var tagged + var control) / 2) over the OOS pairs. If ANY |SMD| > 0.25, the placebo is NOT_DECIDABLE.
- **Decision rule unchanged**: at least 100 OOS pairs and at most 20% of OOS passing events unmatched, AND the balance gate holds.
- **Paired test** (only if decidable): tagged minus control net SOL per trade on each of the four sub-cells; because controls are reused, pairs that share a control are resampled as ONE unit (cluster bootstrap on the control id, 1,000 draws, seed 1, 5th percentile). PASS of this conjunct needs mean > 0 and the cluster lower bound > 0 on every sub-cell.

**Branch A (matcher v2 decidable)**: the placebo is a deciding conjunct of the PASS as in section 5.

**Branch B (matcher v2 NOT_DECIDABLE for any reason: pairs, unmatched share, or balance)**: the placebo becomes REPORT-ONLY. The pairs that exist are reported with their SMDs; no PASS or KILL turns on them. This weakens a pre-declared conjunct and is allowed only because it is fixed here before any price is read. The headline of any result then carries EXACTLY this label and nothing that claims more: "keeper-tagged SL-cascade absorb; the keeper tag is the detector; whether the tag carries information beyond a fast deep dump is UNTESTED (placebo not decidable)". The label "fast-deep sell-dump absorb" is NOT used: it would claim that untagged fast deep dumps behave the same, which was not tested. A forward untagged-dump arm would be a separate arena book with its own registration and bot_id, counted in the DEC-031 multiplicity block, never added in a kill-review week, and never sharing or altering P29's bot_id.

- Disclosures: (a) the untagged-cascade family was already priced on these September days (hunt29-flush-1: "0 of 24 days positive", "after a stop-out cascade the pool keeps drifting"), so controls are already known to be negative there and passing the placebo adds less than it seems to; kills 1-9 still decide. (b) Keeper fills with no create in their slot are untagged by the rule and can sit in the control pool; this contaminates the controls in the conservative direction. (c) The tagged events are fast and deep (median drop 28%, 3 sellers, median 8 s) against untagged bursts (median drop 8%, 56 s): even with v2 the matched controls come from the deep, fast tail, which is why the balance gate exists.

## 7. Multiplicity and winner's curse (QP item 11)
- Deciding cell count k = 1: one event, one exit, one leg (primary), PumpSwap only. The fail models and the strand sub-cells are conjunctive (all must hold), not extra cells. Stress, best case, in-sample, bonding and the placebo arms other than the deciding one are report-only and are not in k.
- Family: at least 16 earlier fade/absorb cells have already failed: dump-absorb-1..6, flush f2/f3/f5, ll-cascade-fade c3/c4/c6, dbc-cascade-fade c3/c4/c6, drain-escape-2. P29 is about the 17th. This is a family of dump-buying rules; the chance that one of 17 passes by luck is not small, and a k = 1 bootstrap does not price that.
- hunt29-flush-1 already priced a stop-out cascade fade on fast-pool-0918 and oracle-insample-0922 (6 of the 11 OOS days; those two are kit canonical screen blocks). The stop -10% and short hold were written with that result in view; this is a disclosed design contamination.
- After the single look, ALL 11 OOS days and the 5 in-sample days are IN-SAMPLE for every later keeper-tag rule (TP mirror, exit or lag variants, a bonding variant). A later keeper-tag rule needs new days.
- Label: a PASS is an "exploration-OOS PASS", not a promotion (section 10).

## 8. Capacity (arithmetic, not a result) (QP item 13)
N = trades per day, stake 0.25 SOL, net edge e per trade after all costs. Upper bound N <= about 225/day (median full-day SL60 clusters passing the 2% bar on fresh-0903, before the distinct-signature, PumpSwap-only, d >= 3% and 360 s filters; the real N is lower and is set by section 9).
| e | N = 100/day | N = 225/day |
| --- | --- | --- |
| 0.5% | 0.125 SOL | 0.28 SOL |
| 1% | 0.25 SOL | 0.56 SOL |
| 2% | 0.50 SOL | 1.1 SOL |
- **C0-capped line (DEC-032 s3): stake 0.05 SOL, at most 2 open, at most 30 buys a day.** At e = 1% that is at most 30 x 0.05 x 1% = 0.015 SOL/day (about $2 at SOL = $150), far below the owner's cash-flow ask ($25-40/day). The book is a capacity-limited one at C0; scale needs the DEC-031 s2 step-4 confirmation and a size ladder, which this file does not declare.
- A 0.25 SOL buy costs roughly 0.6-1.7% impact before exit costs on thin pools; kill 8 (median gross edge > 1%) is set for that reason. Impact is larger at larger stake (capacity-first rule).
- **Concurrency report (report only):** from the priced events, the maximum and the 95th percentile of simultaneously open positions per UTC day, and the implied wallet need at 0.25 SOL stake against the owner's wallet (1.29 SOL total capital on 2026-10-09); a day whose peak concurrency x 0.25 SOL exceeds the wallet is flagged.

## 9. Data coverage gates (QP item 9)
A counts-only dry pass (tools/p29_keeper_coverage.py, reads signatures, times and mints only; no price, quantity or reserve) reports per block and per full UTC day: keeper fill txs; tape rows with a fill signature; distinct fill signatures on tape; ratio = tape rows with a fill signature / keeper fill txs (and the distinct-signature ratio); and the count of events with >= 3 distinct SL fill signatures in 60 s. Listener tapes (fast-pool, oracle-insample) covered 84.8% and 66.2% of chain rows on 2026-10-01 (tape-coverage-chain-2026-10-01.md); a rule needing 3 tagged signatures loses roughly coverage^3 of its events and has gappy paths.
- A block whose **median full-day ratio < 0.85 is dropped from the OOS look** (reported, not read for price).
- If fewer than 100 events or fewer than 5 days remain after dropping, the read is **NOT_DECIDABLE** (no price is read).
- The expected n after the d >= 3%, PumpSwap-only, 360 s-age filters needs a price (drop d); it is computed only in the first step of the real-layout pass, after the QP re-check, as a COUNT (no returns, no entry/exit), and if it leaves < 100 events or < 5 days the read is NOT_DECIDABLE.
- Dry-pass result: see "Coverage dry pass result" at the end of this file.

## 10. Forward path (QP item 15)
- A PASS here only PERMITS registering P29 as an arena book (DEC-031 s1.2 `bot_id`). It does not qualify a canary.
- C0 needs that book's DEC-031 s2 gate read PASS (first 00:00Z with >= 100 counted attempts over >= 5 UTC dates; deciding cell: 1.9 s leg at the pinned stake, flat and pressure, strand rules sc and total; worst-in-slot per DEC-031 Am.15 item 6) AND all of DEC-032 s1. Stake above C0 needs the DEC-031 s2 step-4 confirmation.
- The keeper-mention subscription is a feed/runner change; it needs its md5 decision-equivalence replay proof before deploy (repo rule) and the tag-lag measurement of section 2.
- A forward book refuses V-range pools younger than 360 s in October until EXP-024 Look-2 ends (first 2026-11-14T00Z); the kit's MIN_POOL_AGE_S = 360 is mandatory.
- The screen universe equals what can be registered: PumpSwap, pool age >= 360 s.

## 10b. Report-only coverage lines (QP re-check fix 3) and implementation rulings (v3)
Report-only lines, never a kill, no outcome involved:
1. **exp011-0909-only result**: the full result on all four sub-cells for exp011-0909 ALONE is printed in the headline beside the pooled result. It cannot rescue a pooled kill and is not a separate cell (k = 1 unchanged).
2. **Reserve-disagreement share**: per block, the share of entry and of exit landing slots where consecutive pool prints disagree on reserves (the previous print's post-trade vault or base, in the kit's post-state convention, differs from the next print's pre-trade value by more than 0.5% relative; `tools/p29_keeper_rule.landing_disagree`). The tolerance is a declared report parameter. Entry landing slot = decision slot + D + ceil(1.9 / sps) + 1; exit landing slot = exit decision slot + D + exit lag + 1 (kit `need`).
3. **Listener-tape gap, unexplained**: fast-pool-0918 and oracle-insample-0922 (6 of the 11 OOS days) are listener tapes; the other 5 OOS days (exp011-0909) are a getBlock walk. The tag-coverage ratio of the listener tapes (0.894 and 0.908 medians) is higher than their 2026-10-01 chain coverage (84.8% and 66.2%) would predict, and this gap is UNEXPLAINED. A listener-tape price path may have gaps that the tag-coverage ratio does not show; the exp011-only line above is the guard against it.

Implementation rulings made while building the rule on the kit API (declared before any price is read; each is the closest kit-native form of the section 1-3 text; QP to confirm at the code head):
- A. **Decision slot**: superseded by K (entry exactly at last tagged slot + D + ceil(2.9/sps); exit exactly at trigger + D + ceil(1.9/sps)).
- B. **Reversion and stop use the kit's ExitSpec**: `tp` = R / P_dec (R = P_last + 0.5 (P_pre - P_last); P_dec = V-priced post-trade price of the last print STRICTLY BEFORE the decision slot, the kit's reference), `sl` = 0.90 and `hold_s` = 120 s. The kit measures `tp` and `sl` on that decision-slot spot (post-trade, V included), not on the average entry fill price. This REPLACES the section 3 wording "S = 0.90 x entry fill price"; the stop reference is the decision-slot spot. **Stop-vs-fill drift (disclosure):** the entry fills 7 + ceil(2.9/sps) slots after the last tagged slot, up to that many slots after the decision slot, at a price that can be above or below the decision spot; if the price rises during the latency the stop sits further than 10% below the fill, if it falls it sits closer (or the position is already past it). Edge case 1 (target already met) is `tp <= 1`: skipped and counted. Edge case 2 of section 3 no longer exists. Ties: the EARLIEST PRINT that crosses either level decides (kit rule); tp and sl cannot be the same print. Slots where both a tp-hit and an sl-hit print occurred are counted (report only). Strand rules `total` and `sc` are both priced.
- C. **2% impact bar** is evaluated at the decision-slot state ((post-trade vault + V) / 1e9 >= 25 SOL for a 0.25 SOL stake, the P26 form 2 x 0.25 / R), because the landing-slot state is in the future of the decision. The priced impact at the landing slot is in the P&L (kit). Counted as `impact_bar_2pct`.
- D. **Event per pool**: the 3 distinct signatures must be in ONE pool (events are detected per pool); a mint whose signatures are split across pools with fewer than 3 in each simply has no event. The "span pools" skip of section 1 is therefore automatic.
- E. **Universe**: the kit's Am.2 band (V in [17.5e9, 17.7e9]) and WSOL quote apply. A pool absent from the V map is `v_unknown`; a real V outside the band (including a real 0) is `v_out_of_band`; both are skipped and counted separately. This REPLACES the section 1 sentence "a real V = 0 is used": the kit refuses every V outside the band.
- F. **Untagged bursts (placebo candidates)**: HourRows carries no signature, so a burst is >= 3 sell rows by >= 3 DISTINCT TRADERS within 60 s, none tagged (any kind), 60 s refractory; "distinct sellers" for matching = distinct traders in the window for both tagged and untagged bursts. All other filters are the tagged ones.
- G. **Look-back**: P_pre is the last print at or before (first tagged sell - 60 s) among the pool's rows in the current and the previous loaded hour; none found -> `no_pre_print`, skipped and counted.
- H. **Warm-up hours**: each block is loaded from its first hour through the end of its last declared day (the kit's pool-age and look-back need history); events closing on a day that is not a declared day are neither recorded nor priced. Declared days are exactly the section 4 days.
- I. **Gross edge (kill 8)** = exit spot / entry spot - 1 on the kit's own landing states (Qx/Bx over Qe/Be, V included), per filled trip, before fees, impact beyond spot, and tx cost; the median over the OOS trips.
- J. **Pricing look gates (all checked BEFORE any price is read, in this order; a refusal reads nothing).** (1) `P29_PRICE_AUTH` equals the exact HEAD sha (set by the manager after a reviewer verdict and a QP OK on the code). (2) `git status --porcelain` is empty (a dirty tree is refused). (3) `--coverage` is REQUIRED and its sha256 equals `docs/research/p29-keeper-absorb/coverage.json` at HEAD. (4) The kit pins of section 0 match. (5) An exclusive run-once lock file is created with O_EXCL (default `/data/mal/hunt-1008/push-1016/hunt30/p29/PRICE_READ.lock`, holding head, UTC and job id); if it exists the run is refused; the lock is never released, and a crash after the lock is NOT_DECIDABLE, not a re-run. The run output records the sha256 of `tools/p29_keeper_rule.py`, `tools/p29_keeper_run.py` and `tools/p29_keeper_coverage.py`, the head, and the lock. Each gate has a test. Hours >= 2026-10-02T10 found in an allowlisted directory REFUSE the run (they are not skipped); hours of a block past its last declared day that are earlier than that are simply not opened.
- K. **Timing implemented exactly as section 2.** Entry slot = last tagged slot + D + ceil(2.9 / sps) (decision slot = last tagged slot + ceil(2.9/sps) - ceil(1.9/sps); the kit then adds D + ceil(1.9/sps)). Rounding note: this decision slot can be up to one slot earlier than last tagged slot + ceil(T_tag/sps); the tag knowledge used is the last tagged slot's, the same as in section 2. Exit slot = trigger slot + D + ceil(1.9 / sps): the runner shifts the kit's exit decision slot by ceil(1.9/sps) - ceil(0.55/sps), because the kit then adds D and its own exit lag. Tests: `test_entry_slot_is_exactly_*`, `test_exit_lands_at_*`.
- L. **Report-only legs and concurrency are implemented**: the STRESS leg (entry = last tagged slot + D + ceil(4.9 / sps)), the BEST CASE (entry = last tagged slot + D + 2 slots, the decision slot then lies before the cluster's last tagged slot: this is an informed-instant bound, not a tradable rule) and the concurrency report (per day maximum and 95th percentile of simultaneously open positions and the wallet need at 0.25 SOL) are produced by the pricing look as extra report-only passes; none decides or rescues a kill.
- M. **Verdict statuses**: fewer than 5 traded days or fewer than 100 trades is NOT_DECIDABLE (not a KILL); a decidable result with any of kills 1-9 on any sub-cell is a KILL.

## 11. Process
1. QP re-check on the exact head of this file.
2. Manager records the exp011 line (section 4). Merge this DECLARE.
3. Build the P29 rule in the kit as a MiScusi job (sh, mem <= 6 GB, <= 2 CPU, notebook entry first, one heavy job at a time, 0 Helius, md5 pin check of section 0 files). A counts-only real-layout pass first (events, controls, skips, refusals and the filter count of section 9), then the one OOS look.
4. Seals as in the hunt30 SEALS-BRIEF: no HV4/Q50/DBC forward/arena/meta-capture contact; no hour >= 2026-10-02T10 from any source.
5. The result goes to a lab note with every kill line and every sub-cell; no sentence that a book made money before quant-proof on the exact head.

## Coverage dry pass result (job #1619, 2026-10-25; counts only, no price read; raw: coverage.json)
Ratio = tape rows carrying a keeper fill signature / keeper fill txs that day (rule 2 of DECLARE.md). Events = >= 3 DISTINCT SL fill signatures in one mint-or-pool within 60 s, all venues, before the PumpSwap-only, d >= 3% and 360 s filters (a count of times only).

| Block (role) | Day | Keeper fills | Tape rows | Ratio | Events |
| --- | --- | --- | --- | --- | --- |
| fresh-0903 (in-sample) | 09-04 | 2,087 | 1,868 | 0.895 | 96 |
| | 09-05 | 2,312 | 2,216 | 0.959 | 150 |
| | 09-06 | 3,397 | 3,139 | 0.924 | 227 |
| | 09-07 | 4,545 | 4,231 | 0.931 | 276 |
| | 09-08 | 5,251 | 4,883 | 0.930 | 356 |
| | median | | | **0.930** | |
| exp011-0909 (OOS) | 09-10 | 2,488 | 2,401 | 0.965 | 143 |
| | 09-11 | 1,984 | 1,830 | 0.922 | 118 |
| | 09-12 | 1,957 | 1,815 | 0.927 | 100 |
| | 09-13 | 2,762 | 2,434 | 0.881 | 170 |
| | 09-14 | 1,849 | 1,715 | 0.928 | 84 |
| | median | | | **0.927** | |
| fast-pool-0918 (OOS) | 09-19 | 4,311 | 3,784 | 0.878 | 223 |
| | 09-20 | 4,179 | 3,777 | 0.904 | 225 |
| | 09-21 | 3,930 | 3,514 | 0.894 | 232 |
| | median | | | **0.894** | |
| oracle-insample-0922 (OOS) | 09-22 | 4,114 | 3,736 | 0.908 | 250 |
| | 09-23 | 4,747 | 4,374 | 0.921 | 291 |
| | 09-24 | 5,372 | 4,766 | 0.887 | 321 |
| | median | | | **0.908** | |

No block is below the 0.85 median bar, so none is dropped. OOS: 11 days, 2,157 events before the PumpSwap-only, d >= 3% and 360 s filters. The distinct-signature ratio is within 0.002 of the row ratio, except oracle-insample (0.884-0.917). The lowest single days are 09-19 (0.878) and 09-13 (0.881). The expected n after the price-based filters is still to be counted (section 9, after the QP re-check). The listener tapes score higher than their 2026-10-01 chain coverage (84.8% / 66.2%) would suggest; September coverage may differ from 10-01, and this is not explained here.

## Counts-only pass on the real layout (job #1628, code head b83a9c54, 2026-10-26; no entry, exit, fill or return was computed; raw: counts_pass.json)
The 3% drop filter, the 2% bar and the reversion multiple read post-trade prices as COUNT FILTERS only. Warm-up hours were loaded (fresh-0903 from 09-03T12, exp011-0909 from 09-09T12, fast-pool from 09-18T23); only declared days are counted.

| Block (role) | Declared days | Tagged SL events | After price filters (no pre-print, drop < 3%, 2% bar, target already met) | After kit universe (V band, pool age >= 360 s) = triggers | Skipped: pool age < 360 s | no pre-print | drop < 3% | target already met | V out of band | V unknown |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fresh-0903 (in-sample) | 5 | 796 | 753 | 648 | 95 | 12 | 22 | 9 | 10 | 0 |
| exp011-0909 (OOS) | 5 | 434 | 412 | 331 | 64 | 8 | 12 | 2 | 17 | 0 |
| fast-pool-0918 (OOS) | 3 | 471 | 419 | 350 | 69 | 25 | 22 | 5 | 0 | 0 |
| oracle-insample-0922 (OOS) | 3 | 558 | 517 | 426 | 89 | 19 | 16 | 6 | 2 | 0 |
| **OOS total** | 11 | 1,463 | 1,348 | **1,107** | 222 | 52 | 50 | 13 | 19 | 0 |

- The 2% bar skipped 0 tagged events at the decision-slot state (all tagged pools have reserve >= 25 SOL; median 109 SOL).
- Tagged events per OOS day (after universe): 73, 56, 58, 106, 38 (exp011); 126, 125, 99 (fast-pool); 111, 156, 159 (oracle-insample). Minimum 38, so every OOS day has trades; the kit's validated trigger count per day equals these numbers.
- Missing-V vs real-V: no tagged event hit a pool absent from the V map (`v_unknown` 0); 19 OOS events (29 over all blocks) hit a pool whose real V is outside the Am.2 band (kit refuses). No bonding rows are loaded (PumpSwap only).
- Rule counters: tagged bursts raw 2,259 (all blocks incl. in-sample, declared days) -> 1,755 triggers.
- **Stops**: NOT_DECIDABLE (events < 100 or days < 5) does NOT fire: 1,107 OOS events over 11 days. No block is dropped on coverage (all medians >= 0.85, section "Coverage dry pass result").
- **Placebo: PLACEBO_NOT_DECIDABLE under the declared matching rule.** Untagged candidates are plentiful (about 8,000-13,000 per block-day pass the same filters) but only 44 OOS tagged events (71 over all blocks) get a cross-pool control: 1,063 of 1,107 OOS events (96.0%) are unmatched (cap 20%; need >= 100 pairs). Cause, from the feature distributions (counts and price-derived drop only): the keeper events are fast and deep (median 8 s from first to last sell, exactly 3 distinct sellers at the 10th to 90th percentile, median drop d 28%, 90th percentile 53%), while untagged bursts are slow and shallow (median 56 s, median d 8%, 10th percentile of d 3.7%). Of the 1,755 tagged events, 521 have any candidate within +-2 pts of d (with total sold SOL, duration and sellers already within tolerance) before the one-control-per-event, same-decile and no-reuse rules. The declared matching tolerances were frozen before this; they are NOT changed here. By the declare the book cannot PASS while the placebo is NOT_DECIDABLE. Options for the manager and QP (a new amendment, not applied): widen the d tolerance (e.g. +-5 pts), allow control reuse, or match on d-quantile instead of a fixed band, then re-count (still counts only); or run the priced look report-only without a PASS route.
- Observation, not a result: a median 28% drop inside 60 s of three keeper stop-loss fills means these events are mostly deep dumps in already-falling pools, which is the regime where the earlier untagged cascade fades (hunt29-flush-1) kept drifting down. This is a feature distribution only; no price after the event was read.
