# EXP-014 (plan, exploration stage): migration + 15 min PumpSwap entry selector

| Field | Value |
| --- | --- |
| **Status** | **Exploration plan.** It fixes the trigger, the features, the exit, the screen, the tries cap and the target block before any EXP-014 code or result exists. It is not a pre-registration. A confirmation pre-registration follows only if this screen passes. Revised after a `quant-proof` review of #263. |
| **Date** | 2026-10-03 |
| **Target confirmation block** | `[2026-08-08T12, 2026-08-14T12)` **only** (the second backup block, #261), read once with m = 1, whatever happens to EXP-013. If it is owned or not clean when the pre-registration opens, EXP-014 waits for a new block that is entered in the ledger before it is sealed. **It never takes the 0828 backup block.** |
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
