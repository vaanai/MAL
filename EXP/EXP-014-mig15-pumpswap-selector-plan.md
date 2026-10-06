# EXP-014 (plan, exploration stage): migration + 15 min PumpSwap entry selector

| Field | Value |
| --- | --- |
| **Status** | **Exploration plan; the screen is redefined by [Amendment 7](#amendment-7-2026-10-06-before-any-real-data-run-and-before-any-exp-014-outcome-is-read-the-screen-at-the-labs-current-standard-supersedes-the-data-cost-and-bar-lines).**  It fixes the trigger, the features, the exit, the screen, the tries cap and the target block before any EXP-014 code or result exists. It is not a pre-registration. A confirmation pre-registration follows only if this screen passes. Revised after a `quant-proof` review of #263. |
| **Date** | 2026-10-03 |
| **Target confirmation block** | `[2026-08-08T12, 2026-08-14T12)` **only** (the second backup block, #261), read once with m = 1, whatever happens to EXP-013. If it is owned or not clean when the pre-registration opens, EXP-014 waits for a new block that is entered in the ledger before it is sealed. **It never takes the 0828 backup block.** **Superseded by [Amendment 5](#amendment-5-2026-10-06-before-any-real-data-run-target-block-reassigned-to-exp-015): this block is no longer EXP-014's target.** |
| **Prior odds (manager's honest estimate)** | **About 10%.** See "Prior evidence" below. |

## Prior evidence, stated before any computation

The 15-minute offset was picked from data **twice**:

- **Cost-segments study** (`ARTIFACTS/lab/exploration-cost-segments-2026-09-28.md` §5, pool A, 3 days).
  - Its best cell, out of 15, was mig+15 in the **top fee tier only**: flat +1.825% (CI lower bound −5.034%), pressure +2.092% (CI lower bound −4.487%).
  - That cell had no buy delay and no slippage cap (§2).
  - This plan has **no tier filter**. The unfiltered mig+15 fills on pool A are worse, since the other bands are negative in §4c. The manager has not recomputed that average.
- **Graduated-swing study** (`ARTIFACTS/lab/graduated-swing.md`, Oracle live tape 2026-09-25 07:00–18:25Z, pool B).
  - A LightGBM selector, the same idea as this plan, was already tried.
  - Its best slice was mig+15m, top 20%: n = 26, mean +0.0026, median +0.017, **ex-top-3 −0.003**.

So **this is not the first selector tried after migration.** What is new here: up to 23 screen days (the 9-day pool plus up to 14 August days), of which up to 17 are getBlock, instead of one day; the closed feature set below, and a gate-shaped screen with an out-of-period slice. Unselected waiting after migration lost in every other band (adverse selection), and the getBlock slice has been the weakest source.

## Hypothesis

At mig+15 most new PumpSwap pools have lost their migration pop. A minority are still being bought in a sustained way. A selector on flow that is visible by T might pick them. EXP-012 enters at the migration slot + 1. EXP-014 decides 15 minutes later, on flow EXP-012 never sees. Whether it really picks different mints is tested by item 6.

## Notation

- **d** is the entry slot offset (d = 4 primary; d = 8 for screen item 5; d = 1 reference only).
- **m** is the family size of a confirmation read (m = 1).

## Fixed design (exploration)

1. **Clock.**
   - On every pool, every print's time is set to `block_time × 1000` before any EXP-014 step. Receive time (`t_recv_ms`) is never used.
   - Events are ordered by (t, slot, tx_index, event_index).
   - EXP-012's 18 features are **recomputed on this clock**, not copied from the EXP-012 table.
2. **Migration and T.**
   - Migration is the first `pumpswap` print after a `pump_bonding` print for the mint, in stream order, as `tools/latency_curve.py` `_Mint.add` defines it.
   - mig_t is that print's block time. T = mig_t + 900,000 ms.
   - Only the first migration per mint counts.
3. **Pool.**
   - Prints are restricted to the pool of the migration print (the `pool` field). Dropped prints are counted.
   - If a source lacks `pool`, that is disclosed and counted per day.
4. **Features.**
   - **Window:** the migration print through the last print of that pool with t ≤ T, in stream order, on raw prints (not signature-collapsed).
   - **EXP-012's 18 features at mig_t**, plus the 9 below:
     - `m15_buys`, `m15_sells`: counts by `side`. A row with no `side` is dropped and counted.
     - `m15_net_sol`: Σ buy `sol_lamports` − Σ sell `sol_lamports`, in SOL.
     - `m15_distinct_buyers`: distinct non-null `trader` on buys. The null share per pool is reported.
     - `m15_max_buy_sol`: the largest single buy, or 0 if there are no buys.
     - `m15_ret`: spot from the post-trade reserves at the last print ≤ T, divided by the migration print's spot, minus 1.
     - `m15_mdd`: max over the window of (1 − spot / running max of spot).
     - `m15_tier_ppm`: `pumpswap_sol_fee_ppm(market_cap_sol(quote, base))` from the reserves at the last print ≤ T (`tools/paper_curve_math.py`), not the row's `market_cap_sol` field.
     - `m15_secs_since_last`: (T − t of the last print ≤ T) / 1000. The migration print counts, so the value is ≤ 900.
   - That makes 27 features. **The list is closed.**
   - **PR1 must include a truncation test:** every feature computed on a tape cut at T is byte-identical to the same feature computed on the full tape.
5. **Entry.**
   - S_T is the largest slot of **any** tape print (any mint) with t ≤ T. The entry is this mint's pool state at the start of slot S_T + d (`_state_index`, bound `start`).
   - Landing time is the time of that slot's first print, or else T + d × 400 ms.
   - The buy is `_try_buy`: 0.5 SOL, direct, priority 500,000 lamports per side, the 15% `DEFAULT_SLIPPAGE_CAP` against the spot at T.
   - **MISS** means the cap refused or `quote_buy` returned None. A MISS costs the priority fee and is a label-0 row.
   - There is **no tier filter**.
6. **Exit.**
   - tp50 and sl30 use the `_tpsl` mark, measured from the spot after our buy.
   - The 30-minute cap is landing + 30 min.
   - A tp or sl sell lands at the state at the start of slot (trigger print slot + d) (`_delayed`, bound `start`).
   - A cap sell lands at the start of slot S_cap + d, where S_cap is the largest slot of any tape print with t ≤ the cap instant.
7. **Fail models.** Flat 15%, and the pressure curve at scale 1, both evaluated at the entry state and applied to both legs as expected values (`mixed_net`).
8. **Label.** `1{press > 0}` at d = 4, MISS rows included.
9. **Model.** S2 `lgb_medium` with EXP-012's hyperparameters, seed 1.
10. **Threshold.** The p90 of the pooled OOF scores, `index = round(0.90 (n − 1))`. A row is selected if score ≥ threshold.
11. **Exclusion by trigger time alone.** A row (training or scored) is excluded if `T + 1,800,000 + 2d × 400 + 60,000` ms is at or after the end of its pool run, or at or after the start of the first gap after T. Table-censored rows are counted separately.
12. **Edge days** (report-only) are:
    - (i) the first day of each pool run, because `had_bond` drops mints whose bonding prints come before the run, and the EXP-012 features are truncated there;
    - (ii) the last day of each run.
13. **Data.** The 9-day exploration pool, plus every `explore-0814/wN` view whose `VIEW.sha256` mtime is ≤ **2026-10-05T12:00:00Z**, recorded in the manifest. Never the two backup blocks, the EXP-012 holdout, the EXP-011 block, the EXP-009 block or the forward walk.
14. **Tries: one.**
    - Tries-log key `exp014_m15`. The code refuses if any `exp014_m15` screen entry already exists.
    - A `started` line is written before any model is fit, so a crash still uses up the try.
    - Debug table builds before the cutoff compute no model, LODO or screen output. They are not opened beyond runtime, memory and row counts, and they are deleted before the pin.
    - On a FAIL the family is closed and not re-tuned, at any offset.

## Screen (stated before any computation)

- **When.** Run once, after 2026-10-05T12:00:00Z, with EXP-013 Amendment 5 mechanics: nested LODO; `book_stats` once per fail model; N counts every manifest day for the item; a day with no trade is not positive.
- **Row day.** A row's day for bars 1–3, 1x, 4a and 4b is the UTC day of T. Only item 6 uses the migration day.
- **Precondition.** If fewer than 6 August days are in the manifest, the screen does not run and EXP-014 closes NOT_DECIDABLE. The cutoff is never moved.
- **Pass rule.** It passes only if **all** of these hold under **both** fail models:

1. **Bars 1–3, all screen days.**
   - mean > 0 and 90% CI lower bound > 0 (1,000 draws, seed 1);
   - ex-top-3 > 0;
   - more than half of the days positive.
2. **1x.** Bars 1–3 on all screen days **except pool A days and 2026-09-25**, the days used to pick the offset.
3. **4a.** Bars 1–3 on the getBlock days alone (pool A plus the August views, by pool tag). This is weak, because it includes pool A.
4. **4b.** Bars 1–3 on the August days alone. Disclosed: the `explore-0814/w1` days were already read by DEC-017 candidate (a). Those were slot+1 migrate outcomes on the same mints, with overlapping hold windows.
5. **Item 5.** The pooled mean is > 0 at d = 8 for the same selected mints.
6. **Item 6, overlap with EXP-012 on its 9 OOF days.**
   - **(a) Gating:** J(A, B) ≤ 0.5. A and B are as in EXP-013 Amendment 5 §5, with B restricted to mints that have an EXP-014 row. An empty A ∪ B fails.
   - **(b) Gating:** the EXP-014 selected trades on mints **not** in B have a pooled mean > 0 under both fail models. If no selected trade falls on a mint outside B, 6(b) fails.
   - **(c) Reported:** Pearson and Spearman correlation of daily SOL totals, flat and pressure.
     - EXP-014 is grouped by **migration** day, to line up with EXP-012.
     - A day with no trade counts as 0 SOL.
     - If either series has zero variance, the correlation is reported as undefined.

Bars 1–3 are also reported without edge days. The pass decision uses the full set.

## Pressure model on PumpSwap (disclosed)

- **Out of range at mig+15.** The pressure curve was fitted on migration-instant sends (`TARGET_FAIL_RATE = 0.289`). At mig+15 its inputs are mostly out of that range, so p_fail sits near the intercept floor, about sigmoid(−1.4549) ≈ 0.19.
- **Coarse window.** Its 2-second `nearby` window is only 1-second-coarse on getBlock.
- **Optimistic on losers.** `mixed_net` pulls every outcome toward −priority, so a higher p_fail makes losing trades look better.
- **Failed sells.** A failed sell at sl30 during a PumpSwap dump (stuck, then sold lower) is not modelled.

The flat 15% leg is the cross-check.

## Multiplicity

- **This read.** The confirmation read is m = 1. Entry slot + 4 is its only gating cell, and EXP-013's outcome changes nothing in EXP-014's gate.
- **Joint reviews.** If several families earn forward books, any review that reads them together (with EXP-012's forward book, if they overlap) uses DEC-014 Holm with m = the number of candidates, 10,000 draws, seed 1, under both fail models.
- **Disclosure.** The pre-registration states how many confirmation reads the lab has run so far. Sequential single tests are not covered by DEC-014. The forward stage and owner approval are the guard against that.

## What a PASS earns

A pre-registration on the target block, with `quant-proof` review, for one m = 1 read with the frozen model and threshold. A pass there earns a forward book, never a live trade.

## Build plan (after this plan merges)

1. **PR1.** `tools/exp014_m15_*`: trigger, features, entry and exit, and a table builder with the guards of `tools/exp013_grad_table.py`. Includes the truncation test from design item 4.
2. **PR2.** Reuse `tools/exp013_grad_model.py` through a feature-list or config parameter. The model code is not forked.
3. **PR3.** Reuse `tools/exp013_grad_screen.py` item functions through a config, adding items 1x and 6(a/b), plus a pin and run script.

## Amendment 1 (2026-10-03, before any real-data run): the block clock on pool B

- **Finding (field-presence checks only, no outcome read).** While PR1 (#264) was in review, the manager checked which fields the real rows carry. Pool B rows (the Oracle live view `oracle-live-2026-09-25_27`) have **no `block_time`**. They do carry `event_ts`, the on-chain timestamp in seconds. On pools A and C and on `explore-0814/w1`, `event_ts == block_time` on every one of 50,000 rows sampled per pool. Every PumpSwap row sampled carries `pool` and `slot`.
- **Design item 1 now reads:** every print's time is `block_time × 1000`. Where `block_time` is absent, it is `event_ts × 1000`, the same on-chain second. Receive time (`t_recv_ms`) is never used. A row with neither field is dropped, and the drop is counted per pool and per day.
- **Why this is needed.** Without the rule, pool B would silently contribute no rows. This amendment changes no bar and selects nothing.

## Amendment 2 (2026-10-03, before any real-data run): pool B is excluded

- **Finding (code and field checks only, no outcome read).** Pool B (`oracle-live-2026-09-25_27`) has no on-chain time for **creates**. `adapt_create_row` takes `t_ws`, the PumpPortal websocket receive time. Its trades have only `event_ts`, the on-chain time (Amendment 1). So on pool B, the EXP-012 features that join creates to trades would mix two clocks:
  - `time_to_migrate_s`;
  - `sniper_buy_share`, which uses a 3 s window;
  - creator history.

  Pool B is also the Oracle tape that had lag spikes. Every other source (pool A, pool C, the August views) carries `block_time` on both creates and trades.
- **Decision.** Pool B is excluded from EXP-014's table, training and screen. The screen days are pool A, pool C and the verified August views.
  - The table builder still pins and verifies the pool B root, to keep the guards uniform, but plans no pool B chunk.
  - Item 1x now excludes pool A days and **2026-09-25 within pool C** (C runs to 2026-09-25T06), the days used to pick the offset.
  - Nothing else changes.
- **Why this is not a forking path.** It is a data-quality rule, fixed before any EXP-014 row exists. It drops the source with the weakest clock. Pool B days were part of the graduated-swing study that picked the offset (disclosed above), so excluding them removes some selection, not adds it.

## Amendment 3 (2026-10-03, before any real-data run): item 6(b) is on EXP-012's 9 OOF days

This follows the `quant-proof` review of the screen code (#269). Item 6 is headed "on its 9 OOF days". Item 6(b) is now read that way:

- **Gating:** the EXP-014 selected trades whose **migration day** is in 2026-09-19..2026-09-27 and whose mint is not in B must have a pooled mean > 0 under both fail models. If no such trade exists, 6(b) fails.
- **Reported, not gating:** the same mean over selected trades on any day.

Outside those 9 days B is empty by construction, so the any-day version would let August trades carry the bar. Those are trades EXP-012 was never asked about. This reading is stricter, and no other bar changes.

The screen's `screen.json` also reports:
- censored counts by reason at d = 4 and d = 8, with how many of those EXP-014's own trigger-time rule would have kept;
- Jaccard variants (B restricted to eligible d = 4 mints; unrestricted B; overlap / min).

These are report only.

## Amendment 4 (2026-10-06, before any real-data run and before the EXP-012 backcheck read): V pricing; August bars no longer on unread data

- **V pricing.** Every leg of this plan is on PumpSwap, so every fill, mark and exit is mispriced unless V is added. V is the virtual quote reserve, 17.5845 SOL, already in effect from 2026-08-14 per job #197.
  - The screen prices every PumpSwap print through `tools.pumpswap_virtual_adapter` with `mcap_mode="v"`.
  - The map is `/data/mal/pumpswap-virtual/pool_v_0814.json`, sha256 `2506f7d2d8475e44ca70a8c536dbb7405930b1092edca331dbbe611236b4d2f8`. It is a superset of `pool_v.json`, extended to the August pools by job #196: 34,945 pools, all 20,057 existing entries unchanged, 1 null of the 16,346 August pools, read-only since 2026-10-06T00:30Z.
  - The screen refuses before any outcome is computed if more than 1% of the relevant PumpSwap prints have no V. A null account fetch counts as missing.
  - The run records the adapter counts.
- **Code.** #269 is not merged. The adapter is added there, or in a follow-up PR before the single run, with a test that it is active in the spawned workers. Nothing else in the design changes.
- **Disclosure: August outcomes read before this screen.** The EXP-012 backcheck ([EXP-012-backcheck-0814](EXP-012-backcheck-0814.md), PR #342) reads migrate-entry outcomes on the `explore-0814` days once: 30-minute holds from 4–8 slots after the first PumpSwap print. They overlap this plan's mig+15 entry window on shared mints.
  - Bars 4a and 4b are therefore **no longer on unread data**. That is in addition to the `w1` disclosure in item 4b.
  - No bar is relaxed, and the precondition and every item still gate.
  - `screen.md` must carry this disclosure in its banner.
- **Post-read changes.** Any change to EXP-014 beyond V pricing that merges after the EXP-012 backcheck read counts as post-read. It must be disclosed as post-read in `screen.md` and needs a `quant-proof` pass. The reviewer of the adapter PR checks that its diff is pricing-only.

## Amendment 5 (2026-10-06, before any real-data run): target block reassigned to EXP-015

- **Displacement.** `[2026-08-08T12, 2026-08-14T12)` (the second backup block, sealed and verified 2026-10-04, never read) was this plan's only target confirmation block. The manager reserved it for EXP-015's confirmation instead ([EXP-015 plan v2](EXP-015-pooled-retrain-plan-v2.md) §6, [docs/HOLDOUT_LEDGER.md](../docs/HOLDOUT_LEDGER.md)). It is **no longer EXP-014's target**, in either plan text or ledger.
- **What EXP-014 does now.** It waits for a new block older than 2026-08-08T12 that is walked later (the ledger's "Future fast backfill" row), entered in the ledger before it is sealed, read once with m = 1. It still never takes the 2026-08-28 block, which stays reserved for EXP-013.
- **Nothing else changes.** No screen bar, try cap or design item is edited. The screen itself reads exploration data only and does not need the block.


## Amendment 6 (2026-10-06, before any real-data run): lab-wide α for the confirmation

In addition to the gate, all four unread confirmation families (EXP-013, EXP-014, EXP-015 and the planned EXP-016 rug veto, #381) share the lab's α = 0.05 by Bonferroni, so each confirmation read must also have a one-sided bootstrap p-value of mean > 0 ≤ **0.0125** (= 0.05/4), under both fail models, with the `book_stats` resampler and the date-cluster resampler, 10,000 draws, seed 1, both passing (share of bootstrap means ≤ 0). The reads happen separately on different blocks, so this is per-family Bonferroni, not a Holm step-down. EXP-012's holdout already spent its α. This tightens the confirmation and never loosens it; no screen bar, try or block changes. Written before any of these confirmation blocks was read.

## Amendment 7 (2026-10-06, before any real-data run and before any EXP-014 outcome is read): the screen at the lab's current standard (supersedes the data, cost and bar lines)

EXP-013 is closed (it failed), so the "do not merge before the EXP-013 screen" condition on #269 is gone. #269 predates the PumpSwap V correction, the realistic deciding costs, the EXP-015/016/017 loader lessons and the two-log tries pattern. It is closed and replaced by `tools/exp014_screen_v2.py` (PR linked from #269). Nothing below reads an outcome; the tool's `--precount` and `--guards-only` modes are outcome-blind. No EXP-014 table, model, LODO or screen output exists.

**Prior odds, stated again: about 10 %.** Nothing since the plan raised them. The 15-minute offset was picked from data twice (see "Prior evidence"). The V correction removed most of the PumpSwap edge that EXP-012 appeared to have, and unselected waiting after migration lost in every other band. Expect a FAIL. A FAIL closes the family (item 14).

### What does not change

The trigger (mig + 15 min on the block clock), the 27 features (the list is closed), the pool restriction, the entry at slot S_T + d, the tp50 / sl30 / 30 minute exit, the two fail models, the model recipe (S2 `lgb_medium`, seed 1), the p90 threshold of the pooled OOF scores, `excluded_by_time` and the 4b/6 disclosures above. d = 4 is primary and d = 8 is item 5. d = 1 (reference only) is dropped from the screen.

### Deciding costs (replace item 5's 0.5 SOL / 500,000 and the exit offset)

- **V pricing.** Every PumpSwap print is priced through `tools.pumpswap_virtual_adapter` with `mcap_mode="v"` and the map `/data/mal/pumpswap-virtual/pool_v_0909.json`, sha256 `70914a1619e4cf6adbb1d1981cbd8a49483f559b230e7dcfc224335a0635b42e` (the pin `e15.VMAP_0909_SHA256`; it supersedes `pool_v_0814.json` of Amendment 4). The sha is checked in the parent and again in every worker. The adapter wraps the trigger module's `print_from_trade_row`, the binding the m15 worker uses, and a test asserts it is active in the worker and restored after.
- **Exit lag 2.** A tp / sl sell lands at the start of slot (trigger slot + 2); a cap sell at the start of slot S_cap + 2. The entry offset d stays 4 (and 8 for item 5).
- **Haircut.** The EXP-012 backcheck haircut: net0 is reduced by P x 0.0042038 on a filled trade (sell shortfall 16 bps, entry gap 26.08 bps), through `e15.cell_nets`. A MISS pays one fee.
- **Fee.** 505,000 lamports per side, both fail models (flat 15 %, and the pressure curve at scale 1). The table's own `flat` / `press` (500,000, no haircut) are replaced by these before the label `1{press > 0}` is formed.
- **Stake.** **0.05 SOL decides**, to match the EXP-015 cache bars. 0.5 SOL is simulated in the same pass on the same trigger and the same selected mints and is **reported, not gating** (the model trains and selects on the 0.05 SOL label).

### Views (replace item 13)

The 27 non-P1 dates, as EXP-015 / EXP-017: P2 `explore-0814` (w1..w7, 2026-08-15..08-28, 14 dates, 24 h feature buffer from 08-14T12), P3 `fresh-0903` (the `.deduped.jsonl.zst` copies through `e15.guard_p3` / `make_p3_hours`, 7 dates) and P4 `exp011-0909` (b, c; 2026-09-09..09-15, 7 dates, 09-09 shared with P3 = 13 September dates). Rows are those whose migration is inside the block's counted window; a row's day is the UTC day of T. **P1 is not read at all** (report-only means a printed "not read"). Pool B is irrelevant (P1). No reserved block, the EXP-012 holdout, the 0808 / 0828 blocks or the forward walk is opened (`e15.refuse_reserved`, `e15.assert_hours_allowed`). The old "9-day pool plus August views by mtime" rule and Amendments 1 and 2 (pool B clock) no longer apply because none of those sources is read.

### Bars (replace the Screen's items 1-4b; item 5 and item 6 adapted)

All under **both** fail models, at 0.05 SOL, on the nested-LODO selected rows, with the gate's own `book_stats` and the date-cluster resampler both passing (CI lower bound > 0; 1,000 draws, seed 1):

1. **Bar 1.** All 27 dates: n >= 100, >= 5 dates with trades, a majority of the 27 dates positive (a date with no trade is not positive), CI lower bound > 0, ex-top-3 > 0.
2. **Bar 2.** The same gate on the 13 September dates (P3 + P4) alone.
3. **Bar 3.** Paired against entering every eligible mig+15 trigger at the same costs: x = (selected - 1) x net per trigger; mean > 0, date-cluster CI lower bound > 0, ex-top-3 of x > 0. (EXP-015's bar 3 was paired against the frozen EXP-012 book; that book enters at migration, a different trigger, so the baseline here is the unselected mig+15 book the plan says loses. EXP-012 overlap is item 6.)
4. **Bar 4.** Concentration: no date above 20 % of the positive-date total, and total excluding the best date > 0.
5. **Bar 5.** P2 + P4 only: mean > 0.
6. **Bar 6.** Transfer: fit on the September dates only, threshold = p90 of the pooled inner LODO scores of those dates, score the August (P2) rows: mean > 0 and more than half of the 14 August dates positive.
7. **Item 5.** The same selected mints at d = 8, pooled mean > 0.
8. **Item 6 (adapted).** EXP-012's 9 OOF days are P1, which is not read, so the overlap is taken against the frozen EXP-012 selection on the 27 dates (the cached EXP-015 rows, features only; net fields are dropped at parse time). A = EXP-014 selected mints; B = the frozen selection restricted to mints with an EXP-014 d = 4 row. (a) J(A, B) <= 0.5, an empty union fails; (b) the selected trades on mints not in B have a pooled mean > 0 under both fail models. Overlap over min size and the correlations are not computed.
9. **Holm, k = 1.** The one-sided date-cluster bootstrap p of mean > 0 (max over the two legs; 10,000 draws, seed 1) must be <= 0.05. This is the screen's own test. Amendment 6's 0.0125 applies to the later confirmation read, not here.

PASS requires every one of bars 1-6, item 5, item 6 and Holm. Anything else is a FAIL and closes the family. The 0.5 SOL stake, the enter-all mean and the bars without edge days are reported only.

### Tries (replace the cap text)

One try, key `exp014_m15`. The tool refuses if either log (the ops log given by `--tries-log` and the canonical `data/tries.jsonl`) already holds a line with that key, takes an O_EXCL `RUN.lock`, and writes a `started` line to both logs before any tape pass (the pass computes outcomes), so a crash still spends the try. A second run is refused before and after `started`. Holm k = 1.

### Pre-declared refusals (before `started`: no try spent)

- Any pin, VIEW.sha256, P3 dedupe manifest or tiling failure; a reserved path; a V map whose sha is not the pin.
- **Any hour with no trades file** in P2, P3 or P4 on the real layout. More than 5 % of hours with no creates file.
- A `zstd -dc` that does not end rc 0 on any file (truncated or corrupt): the pass raises, it never returns a short hour.
- More than 0.01 % of lines not parseable JSON; any PumpSwap row without a `pool`; no clock on a row is counted per day and reported.
- **V coverage:** more than 1 % of PumpSwap prints on a pool with no V; more than 0.5 % of the migrating mints with a create on a pool with no V (below that, those mints are removed and counted, and the bias may run upward; they could be rugs).
- Zero migrations with a create in a block.
- Screen mode without a clean `precount.json` made by the same code, views and V map (a digest of both).
- After `started` (the try is spent, the status goes to the logs): V missing above 1 % in the tape pass, or `rows_after_scored_within_bound` above 0.

### Precount first

`--precount` runs before the screen, on the real layout, outcome-blind. It reads every hour once and counts rows, bad lines, rows without a clock, PumpSwap rows with no pool or no V, migrations in tape order per T day, creates, and the row ceiling (migrations x 2 d x 2 sizes). It computes no price, fill or net. The tape-order migration count is an approximation of the worker's own; the worker's count in `screen.json` is authoritative. The lesson is EXP-016/017's: fixtures miss loader bugs, and a count on the real layout is cheap next to a spent try.

### Disclosures

- The `explore-0814` days were read by DEC-017 (a) and by the EXP-012 backcheck (migrate-entry outcomes on shared mints, overlapping windows); `fresh-0903` was spent by EXP-012's one read; `exp011-0909` by EXP-011 and EXP-015. None of these bars is on unread data. All sources are exploration pool; a pass earns a pre-registration on a new block older than 2026-08-02T12 that is entered in the ledger first (Amendment 5), never a book.
- Nothing is changed in the trigger, features, model or exit rule after the EXP-012 backcheck read other than the sizes / exit-lag parameters (defaults unchanged, so EXP-013's and the table builder's outputs are byte-identical) and the V binding. Under Amendment 4 this is post-read and needs a `quant-proof` pass before the run.
