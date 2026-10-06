# EXP-012 back-check on explore-0814 (pre-declaration, exploration only)

Written before any run. **Amendment 1, 2026-10-06, before any outcome was computed:** primary moved to exit lag 2 after the owner's reviewer flagged it; job #201 was cancelled in the pre-pass (no out-dir, no rows, no tries). Tool: `tools/exp012_backcheck.py`. Best-of-N context. Not a promote, not gate evidence, never a confirmation holdout.

## Question

The frozen EXP-012 entry model (threshold 0.8031, model md5 `a1810d219ed61db64a396f40dc302ce5`, never refit) was frozen on a 9-day exploration pool. What does that frozen strategy earn at the live operating point (realistic exit, 2-slot exit lag) on 14 days the model never trained on? The answer is context for the live probe's size step ([DEC-020](../DEC/DEC-020-size-step-proposal.md)) and nothing else.

## What these days are NOT

They are **not unread**. `explore-0814/w1` `[2026-08-26T12, 2026-08-28T12)` was outcome-read by DEC-017 candidate (a) (`ARTIFACTS/lab/dec017-candidates-2026-10-02.md`, row "getblock_only (August w1)"; EXP-013 Amendment 5). That candidate (job #75) read w1 outcomes in three screens: "getblock_only (August w1)", "august → september" and "september → august"; it is counted as 1 try. The explore-0814 try count therefore starts at **1** and this run adds 6 cells (cumulative 1 + 6 = 7). After this run, the EXP-013/EXP-014 August bars are no longer on unread data; their frozen screens are unchanged but must record that this read happened. The report also gives the primary cell **without w1's hours**, report-only.

## Data

Clean views `/data/mal/clean-view/explore-0814/w1..w7`, each verified against its `VIEW.sha256`. The first 24 h (to 2026-08-15T12) are feature buffer only: migrations before 2026-08-15T12 are scored but not counted. Counted window: 2026-08-15T12 to 2026-08-28T12, **13 counted days**. counted day = 24 h window from 12:00Z, not the gate's UTC day; bar 2 is gate-shaped, not the gate. The table is built by the frozen code path (`exp012_score.load_rows`, anchored chunk plan, `BUFFER_HOURS=24`, `MAX_HOME_HOURS=12`) and scored by the **frozen** model, not out-of-fold scores.

Before any scoring or P&L, a pre-pass reads only the pool field of PumpSwap prints for the mints that MIGRATE in [2026-08-15T12, 2026-08-28T12) (first PumpSwap print in the window, in the views' `migrations/` set, not the wSOL mint; pools of non-migrating mints and of mints that migrated before the window are excluded) and checks the V map. The run exits 2 if more than 1% of those prints have no V. After the pass and before `report.json`/`report.md` are written, it also counts primary-cell trades whose pool the V adapter priced without V, prints the count, and exits 2 if it is above 0. The `--vmap` file's sha256 is asserted before any row is read and recorded in the report. A pool whose map value is null (account missing) is MISSING, never V-less and never V = 0; an explicit 0 is a real V = 0. The coverage line (prints, covered, missing, missing pools) goes to stderr and into the report. The model md5 is asserted before any row is read, and `git rev-parse HEAD` is recorded in the report.

## Cells (6; declared here and in the module docstring; a test asserts they match the code)

Frozen threshold 0.8031, fee 505,000 lamports per side, V pricing, tp50_sl30, 30-minute exit cap. **k counts from the first PumpSwap print, not from migration.** A MISS pays the fee. Both fail models: flat 15% and pressure at slope scale 1.

| Role | k | Size (SOL) | Exit lag | Label |
| --- | --- | --- | --- | --- |
| **PRIMARY** | 6 | 0.05 | 2 | realistic exit (the #329/#339 exit re-check, job #180); still optimistic versus the measured live exit leak |
| sensitivity | 6 | 0.05 | 0 | optimistic exit (upper bound); the only cell reported at lag 0 |
| sensitivity | 6 | 0.25 | 2 | mechanical (fee arithmetic + modelled AMM impact), not evidence; cannot support any live size (DEC-020 §1) |
| sensitivity | 6 | 0.5 | 2 | same as above |
| sensitivity | 4 | 0.05 | 2 | |
| sensitivity | 8 | 0.05 | 2 | |

Exit lag is `exit_land_k` (slots the sell lands after the trigger). Sensitivities are report-only, never selected among, and cannot replace the primary. The 0.25 and 0.5 cells are reported as percent of size. Sell shortfall (-11..-16 bps), entry noise (+-300 bps) and MEV are not added. Primary exit lag 2 slots; still optimistic versus the measured live exit leak; the lag-0 cell is an upper bound.

Reported per cell and fail model: n entered, filled, miss, counted days with days positive, mean SOL per trade, CI90 (gate cluster bootstrap, 1,000 draws, seed 1, 5th-95th percentile of the mean), total, total ex-top-3, tp/sl/time-stop counts, tp rate among filled. For the primary also: per-day table, first-7 vs last-6 days (report-only), the primary without w1 (report-only), and the sharp-drop rate under the #336 label (report-only; it measures post-migration volatility, not rugs).

## What each outcome would mean (declared in advance)

The bars are gate-shaped. "Pass" below means all of these, **under both fail models** (flat 15% and pressure), on the primary cell:

1. n entered >= 100;
2. at least 5 counted days, with a majority of days positive;
3. CI90 lower bound of the mean SOL per trade > 0;
4. total SOL ex-top-3 > 0.

Outcomes, applied in this order (first match wins):

1. **n entered < 100 (primary):** inconclusive. No statement about the size step either way; the CI is not read.
2. **Both legs' means < 0 (flat and pressure):** strong caution against any size step. The in-pool result was likely winner's curse.
3. **Every bar passes under BOTH fail models:** consistent with EXP-012 still working on older days at the realistic exit. Context only for DEC-020: no simulation can say whether 0.25 SOL works (DEC-020 §1), and Option A (after the 10-16 forward read) remains the recommendation. It does not authorise any size and cannot be cited as evidence for EXP-012.
4. **Anything else, including pressure passes but flat fails (or the flat mean < 0), or either leg misses a bar:** does not support. Report as mixed; the size-step case rests on DEC-020's own arithmetic, not on this run.
5. **First-7 and last-6 halves disagree in sign, or the primary without w1 differs in sign from the full primary:** the result is unstable and is read as weaker than the pooled number either way.
6. **V coverage over the limit, or any primary trade on a no-V pool:** the run refuses (exit 2). A refusal after rows were read logs all six cells to the tries logs with status `refused_after_read` (an exception after rows start: `aborted_after_read`) and writes no `report.json`.

**This is never a promote.** The only evidence for EXP-012 is the 10-16 forward read and the promotion gate on a fresh holdout. Any edge claim still goes through `quant-proof`.

## Honest limits

- Exploration pool, best-of-N: all six cells are logged under pool `explore-0814` (count starts at 1 for DEC-017 (a)) in `--tries-log` and the repo's `data/tries.jsonl`, including when a run stops after reading rows (status field).
- V map: `/data/mal/pumpswap-virtual/pool_v_0814.json`, sha256 `2506f7d2d8475e44ca70a8c536dbb7405930b1092edca331dbbe611236b4d2f8` (job #196: 34,945 pools, 1 null of the 16,346 August pools). The pre-pass and the no-V trade count still guard against that one null pool.
