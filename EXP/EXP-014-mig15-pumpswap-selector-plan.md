# EXP-014 (plan, exploration stage): migration + 15 min PumpSwap entry selector

| Field | Value |
| --- | --- |
| **Status** | **Exploration plan.** It fixes the trigger, the features, the exit, the screen and the tries cap before any EXP-014 code or result exists. It is not a pre-registration. A confirmation pre-registration follows only if this screen passes. |
| **Date** | 2026-10-03 |
| **Why this family** | Every family that survived in this lab used a learned entry selector (EXP-012). The cost-segments study (`ARTIFACTS/lab/exploration-cost-segments-2026-09-28.md` §5, §6.2–6.3) found one PumpSwap cell with positive mean net under both fail models: migration + 15 min, top fee tier. Its CI lower bounds were below 0, it had 3 days, and it was the best of 15 cells. That note says a delayed entry "would need to be paired with a signal that predicts *which* delayed entries avoid the adverse-selection cost". This plan builds that signal. No selector has been tried after migration. The one-day swing study (`graduated-swing.md`) and the cost-segments cells were unselected rules. |
| **Target confirmation block** | The reserved, unread second backup block `[2026-08-08T12, 2026-08-14T12)` (docs/HOLDOUT_LEDGER.md, #261), read once with k = 1, if and only if this screen passes **and** that block is still unowned at the time. If EXP-013 has failed, the backup block `[2026-08-28T12, 2026-09-03T12)` may be used instead. The pre-registration names one block before any read. |
| **Prior odds (manager's honest estimate)** | About 15% or less. Unselected waiting after migration lost in every band except the one weak cell (adverse selection), the getBlock slice has been the weakest source, and the 15-minute offset was itself chosen from the exploration pool (winner's curse, disclosed below). |

## Hypothesis

At migration + 15 min, most new PumpSwap pools have already lost their migration pop. A minority are still being bought in a sustained way. Whether a pool keeps rising after 15 minutes should depend on flow the market can already see:
- buy and sell counts in [mig, mig + 15 min);
- net SOL in;
- the number of distinct buyers;
- the price path since migration;
- the fee tier.

A selector trained on those features picks the minority, so the entry pays the cheaper tier without the average adverse selection.

How it differs from EXP-012:
- **Trigger time.** EXP-012 enters at the migration slot + 1. EXP-014 decides 15 minutes later, on flow EXP-012 never sees.
- **Holding window.** The two overlap only for EXP-012 positions still open after 15 min, which have not hit tp/sl and are under its 30-minute cap.
- **Measured overlap.** Mint Jaccard and the daily-PnL correlation with EXP-012 are measured (screen item 6).

## Fixed design (exploration)

- **Trigger:** each migration with a PumpSwap pool, at T = `mig_ms` + 900,000 ms (exactly 15 min, the chain clock of the migration event). Only the first migration per mint counts.
- **Features (all causal at T, events with `t_ms ≤ T`):**
  - EXP-012's 18 frozen features at the migration, unchanged.
  - Plus these, over [mig, T):
    - PumpSwap buy count and sell count;
    - net SOL in;
    - distinct buyers;
    - largest single buy in SOL;
    - price at T over the first post-migration price, minus 1;
    - maximum drawdown from the post-migration high;
    - the fee tier at T, as a ppm value from the PumpSwap tier table in `tools/paper_curve_math.py`;
    - seconds since the last trade before T.
  - That is 27 features in total. The list is closed: no feature is added after the first real-data table.
- **Model:** S2 `lgb_medium`, EXP-012's hyperparameters, seed 1. The label is `1{press > 0}` at the primary k. The threshold is the 90th percentile of the pooled OOF scores.
- **Entry:** a PumpSwap buy at the first slot ≥ (the slot of the last print at or before T) + k.
  - **k = 4 is primary.** k = 8 is screen item 5. k = 1 is reported as a reference.
  - 0.5 SOL, direct, priority 500,000 lamports per side.
  - The fee is the tier at the entry state, as in the existing PumpSwap pricing (`tools/latency_curve.py`, `tools/paper_curve_math.py`).
- **Exit:**
  - tp50_sl30 on the PumpSwap price, from the entry fill;
  - a 30-minute cap;
  - every exit's sell lands k slots after its trigger print, or after the cap instant.
- **Fail models:** flat 15% and the pressure curve at scale 1, evaluated at the entry state and applied to both legs, as expected values (`mixed_net`). A MISS (no pool state, or the slippage cap at entry) costs the priority fee and is a label-0 row.
- **Exclusion by trigger time alone (as in EXP-013 Amendment 5 §1):** a row is excluded if `T + 1,800,000 ms + (2k) × slot_ms` is at or after the end of its pool run, or the first gap after T. slot_ms is 400 for this bound, which is conservative. Table-censored rows are counted separately.
- **Data:** the 9-day exploration pool plus every `explore-0814/wN` view with `VIEW.sha256` written by the screen cutoff in §Screen. Never the two backup blocks, the EXP-012 holdout, the EXP-011 block, the EXP-009 block or the forward walk.
- **Tries cap: one.** A try is one configuration screened on real data, and this plan fixes exactly one. A `started` line goes into the tries log before any model is fit. A crash still uses the try. On a FAIL the family is closed and not re-tuned, including other offsets than 15 min.

## Screen (stated before any computation)

These are EXP-013's Amendment 5 mechanics, reused unchanged, with the same tools pattern.
- **Run date:** the screen runs once, after `2026-10-05T12:00:00Z`, on the pinned views verified by then.
- **Selection:** nested leave-one-day-out over all screen days (trigger day = the day of T). It passes only if **all** of these hold under **both** fail models:

1. **Bars 1–3** via `book_stats`, once per fail model:
   - mean > 0, and 90% CI lower bound > 0 (1,000 draws, seed 1);
   - ex-top-3 > 0;
   - positive days × 2 > N, where N is every manifest day for the item, and a day with no trade counts as not positive.
2. **4a.** Bars 1–3 again on the getBlock days alone: pool A plus the August views, by pool tag.
3. **4b.** Bars 1–3 again on the August days alone. **No EXP-014 question has been asked of the August days**, so 4b is the cleanest out-of-period slice here.
4. **Item 5.** The pooled mean stays > 0 at k = 8 for the same selected mints.
5. **Item 6.** Overlap with EXP-012 on its 9 OOF days:
   - the mint Jaccard (as in EXP-013 Amendment 5 §5) is reported;
   - **the daily-PnL correlation with EXP-012 (flat) must be ≤ 0.7**, so the book adds diversification and is not EXP-012 again. The mint Jaccard is not a bar here, because both books trade the same migrated mints by design.

Every item is computed on the pinned manifest. Bars 1–3 are also reported without edge-flagged days. The pass decision uses the full set.

## Disclosures (before any data)

- **The 15-minute offset was chosen from data.** It was the best cell of 15 in the cost-segments study, on 3 fast-pool (pool A) days. That is why pool A days are in 4a but are not the deciding slice. 4b (August only) never saw that study.
- **Overlap with exploration.** EXP-012's frozen features were selected on the 9-day pool, and that pool is reused here. That is exploration on exploration data, which is allowed. The confirmation read is the only test.
- **The getBlock clock is block time,** at 1-second resolution, with no receive time. T and the entry slot come from chain time. The live latency at a 15-minute decision is less critical than at migration, and k = 8 covers it.

## What a PASS earns

Only a pre-registration (`quant-proof` reviewed) for a k = 1 one-shot read on one named reserved block, read with the frozen model and threshold. A pass there earns a forward book, never a live trade, under the same promotion gate and owner approval.

## Build plan (after this plan merges)

1. **PR1:** a trigger, feature and exit table builder (`tools/exp014_m15_*`), with the guards from `tools/exp013_grad_table.py` (roots, hour fence, extra views, settings, output).
2. **PR2:** reuse `tools/exp013_grad_model.py` through a feature-list parameter, or a thin wrapper. The model code is not forked.
3. **PR3:** reuse `tools/exp013_grad_screen.py` items through a config, plus the item 6 change, the cutoff and a pin/run script.

Table builds before the cutoff are for debugging only: no model, no LODO and no screen output is computed or read from them.
