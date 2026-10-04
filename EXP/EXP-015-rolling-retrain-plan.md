# EXP-015 (plan, exploration stage): EXP-012's recipe, retrained on a rolling recent window

| Field | Value |
| --- | --- |
| **Status** | **Exploration plan.** It fixes the design, the screen, the tries cap and the target block before any EXP-015 code or result exists. It is not a pre-registration. |
| **Date** | 2026-10-04 |
| **Why** | EXP-012's mechanism is the only one in this lab that passed a read on unseen data (entry selection at the migrate trigger). Its main recorded weakness is **decay**: its one-shot read's "returns fade toward the latest day", and the frozen model is trained once on 2026-09-19..27. In a live book, a model that ages is the most likely way a real edge stops paying. This family tests the obvious fix: the **same** recipe, refit every day on only the most recent days, evaluated strictly walk-forward (train on the past, trade the next day). It is a step forward on the proven mechanism, not a new mechanism. |
| **Owner direction** | 2026-10-04: get to profit fast; build backups to EXP-012 deliberately, from past lessons. |
| **Target confirmation block** | `[2026-08-28T12, 2026-09-03T12)` (the backup block `fresh-0828`). It lies immediately after the August exploration days, so a rolling model trained on August can be walked forward into it day by day. It is used **only if** EXP-013's screen (job #102, 2026-10-04) does not claim it. If EXP-013 claims it, EXP-015's confirmation is the first forward-walk window after EXP-012's FINAL read, pre-registered before that window starts. |
| **Prior (manager's estimate)** | About 25–30%. The mechanism already passed once. The open question is whether recency adds anything or only adds variance, and the walk-forward window sizes are small. |

## Fixed design

1. **Recipe.** It is EXP-012 §3.1 verbatim:
   - the 18 frozen features;
   - S2 `lgb_medium`, seed 1, `scale_pos_weight = neg/pos`;
   - label `1{press > 0}` on `tp50_sl30`;
   - `migrate` trigger, slot+1 start, direct, 0.5 SOL, priority 500,000 lamports per side;
   - both fail models;
   - the table build of EXP-012 (and DEC-017 (a)'s `tools/exp013_refit.py` table path for the extra views).

   No feature, parameter, exit or threshold rule is changed.
2. **Rolling window.** For each test day d (UTC):
   - **Training rows:** every table row whose migration day is one of the **W = 7 most recent pool days strictly before d** (days present in the pool, not necessarily contiguous), **and** whose `mig_ms + 2,100,000` (35 min, beyond the 30-minute exit cap) is before the start of d.
   - **W is fixed at 7.** No other window is tried.
3. **Threshold, per test day.** The p90 of the pooled inner leave-one-day-out OOF scores over those 7 training days, with EXP-012's non-interpolating rule. A row is entered iff `score ≥ that day's threshold`, scored by the model fit on all 7 training days.
4. **Test days.** Every pool day that has 7 pool days before it:
   - **August:** 2026-08-21 .. 2026-08-28, where day 08-28 is partial and ends at 12:00.
   - **September:** 2026-09-19 .. 2026-09-27. Their windows reach back across the gap into the latest August days. This is disclosed and is a real staleness test.
5. **Data.**
   - The 9-day pool A/C/B, exactly EXP-012's clean views.
   - `explore-0814` w1–w7 (all verified 2026-10-03, VIEW.sha256 mtimes recorded in the manifest).
   - Never the two backup blocks, the EXP-012 holdout, the EXP-011 block, the EXP-009 block or the forward walk.
   - Clocks are EXP-012's, unchanged.
6. **Tries: one.**
   - The tries-log key is `exp015_roll`. A `started` line is written before any fit.
   - The run is refused if any prior `exp015_roll` entry exists.
   - A FAIL closes the family, and no other W is tried.

## Screen (stated before any computation)

The screen is the walk-forward book over all test days. It passes only if **all** of the following hold under **both** fail models. Bars 1–3 use `book_stats` once per fail model, and the denominator N is every test day; a day with no trade counts as not positive.

1. **Bars 1–3:** mean > 0 and 90% CI lower bound > 0 (1,000 draws, seed 1); ex-top-3 > 0; more than half of the days positive.
2. **4b.** Bars 1–3 on the August test days alone. These are getBlock-only days, never trained on by EXP-012.
3. **Item 5.** The pooled mean is > 0 at entry slot + 4, for the same selected mints.
4. **Item 7 (the reason for this family).** Over the August test days, under both fail models, the rolling book's pooled mean minus the mean of **EXP-012's frozen model** at its frozen threshold on the same days must be > 0. EXP-012 never trained on August, so this is a fair comparison. The difference is computed per mint as rolling-selected minus frozen-selected pooled means. The bootstrap CI is reported, not gated.

If item 7 fails, recency does not beat the frozen model, and the family is closed even if bars 1–3 pass. In that case the frozen EXP-012 remains the better book.

## Disclosures (before any data)

- **Prior reads of these days.** EXP-012's frozen threshold and features were chosen on the 9 September days. The August `w1` days were read by DEC-017 (a). Its other August days were never read by any EXP-012 question.
- **Gap across August and September.** The September test days' windows span the gap `[2026-08-28T12, 2026-09-19T01)`, which is 3 weeks. That penalises a rolling model, and the plan accepts it.
- **Coverage.** About 17 test days, of which about 8 are August.

## What a PASS earns

A pre-registration (`quant-proof` reviewed) of a walk-forward one-shot read on the target block:
- each confirmation day is scored by a model trained on the 7 most recent available days strictly before it, with its own inner-LODO p90 threshold;
- m = 1, entry slot+1 is the gating cell, and the read happens once;
- on a pass, a forward paper book.

It never adds to EXP-012's forward read, which stays k = 1 (DEC-017 rules apply to any joint review).

## Build plan

1. **Table.** Reuse `tools/exp013_refit.py`'s table path, with EXP-012's recipe and `--extra-fast-view w1..w7`. It builds the EXP-012-recipe table on all 23 days. Its single-pass, final-flush scoring has no mid-file sweep, so the out-of-order-row bug does not apply.
2. **Walk-forward.** A walk-forward screen, `tools/exp015_roll_screen.py`, importing `tools.exp011_freeze` `_fit`/`_predict`/`_percentile` unchanged, with EXP-013 Amendment 5-style guards (tries log, once-only ledger, manifest pin, outputs written before result.v1).
3. **Debug builds** compute the table only. They are not opened beyond runtime, memory and counts, and they are deleted by the owner before the run.
