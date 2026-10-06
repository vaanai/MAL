# EXP-012 back-check on explore-0814 (pre-declaration, exploration only)

Written before any run. **Amendment 1, 2026-10-06, before any outcome was computed:** primary moved to exit lag 2 after the owner's reviewer flagged it; job #201 cancelled 00:53Z; read pool fields only; no price, score or P&L (no out-dir, no rows, no tries). **Amendment 2, same day, still before any outcome was computed:** the outcome rules are read on the primary net of the measured live costs (haircut below), rule 2 is strict, the rules are reordered with a precondition and a modifier, and the tool writes the matched rule. Tool: `tools/exp012_backcheck.py`. Best-of-N context. Not a promote, not gate evidence, never a confirmation holdout.

## Question

The frozen EXP-012 entry model (threshold 0.8031, model md5 `a1810d219ed61db64a396f40dc302ce5`, never refit) was frozen on a 9-day exploration pool. What does that frozen strategy earn at the live operating point (realistic exit, 2-slot exit lag) on 14 days the model never trained on? The answer is context for the live probe's size step ([DEC-020](../DEC/DEC-020-size-step-proposal.md)) and nothing else.

## What these days are NOT

They are **not unread**. `explore-0814/w1` `[2026-08-26T12, 2026-08-28T12)` was outcome-read by DEC-017 candidate (a) (`ARTIFACTS/lab/dec017-candidates-2026-10-02.md`, row "getblock_only (August w1)"; EXP-013 Amendment 5). That candidate (job #75) read w1 outcomes in three screens: "getblock_only (August w1)", "august → september" and "september → august"; it is counted as 1 try. The explore-0814 try count therefore starts at **1** and this run adds 6 cells (cumulative 1 + 6 = 7). After this run, the EXP-013/EXP-014 August bars are no longer on unread data; their frozen screens are unchanged but must record that this read happened. The report also gives the primary cell **without w1's hours**, report-only.

## Data

Clean views `/data/mal/clean-view/explore-0814/w1..w7`, each verified against its `VIEW.sha256`. The first 24 h (to 2026-08-15T12) are feature buffer only: migrations before 2026-08-15T12 are scored but not counted. Counted window: 2026-08-15T12 to 2026-08-28T12, **13 counted days**. counted day = 24 h window from 12:00Z, not the gate's UTC day; bar 2 is gate-shaped, not the gate. The table is built by the frozen code path (`exp012_score.load_rows`, anchored chunk plan, `BUFFER_HOURS=24`, `MAX_HOME_HOURS=12`) and scored by the **frozen** model, not out-of-fold scores.

Before any scoring or P&L, a pre-pass reads only the pool field of PumpSwap prints for the mints that MIGRATE in [2026-08-15T12, 2026-08-28T12) (first PumpSwap print in the window, in the views' `migrations/` set, not the wSOL mint; pools of non-migrating mints and of mints that migrated before the window are excluded) and checks the V map. The run exits 2 if more than 1% of those prints have no V. After the pass and before `report.json`/`report.md` are written, it also counts primary-cell trades whose pool the V adapter priced without V, prints the count, and exits 2 if it is above 0. The `--vmap` file's sha256 is asserted before any row is read and recorded in the report. A pool whose map value is null (account missing) is MISSING, never V-less and never V = 0; an explicit 0 is a real V = 0. The coverage line (prints, covered, missing, missing pools) goes to stderr and into the report. The model md5 is asserted before any row is read, and `git rev-parse HEAD` is recorded in the report.

## Cells (6; declared here and in the module docstring; a test asserts they match the code)

Frozen threshold 0.8031, fee 505,000 lamports per side (500,000 priority + 5,000 base, DEC-019 Am.1), V pricing, tp50_sl30, 30-minute exit cap. **k counts from the first PumpSwap print, not from migration.** A MISS pays the fee. Both fail models: flat 15% and pressure at slope scale 1.

| Role | k | Size (SOL) | Exit lag | Label |
| --- | --- | --- | --- | --- |
| **PRIMARY** | 6 | 0.05 | 2 | realistic exit (the #329/#339 exit re-check, job #180); still optimistic versus the measured live exit leak |
| sensitivity | 6 | 0.05 | 0 | optimistic exit (upper bound); the only cell reported at lag 0 |
| sensitivity | 6 | 0.25 | 2 | mechanical (fee arithmetic + modelled AMM impact), not evidence; cannot support any live size (DEC-020 §1) |
| sensitivity | 6 | 0.5 | 2 | same as above |
| sensitivity | 4 | 0.05 | 2 | |
| sensitivity | 8 | 0.05 | 2 | |

Exit lag is `exit_land_k` (slots the sell lands after the trigger). Sensitivities are report-only, never selected among, and cannot replace the primary. The 0.25 and 0.5 cells are reported as percent of size. The deciding read nets the sell shortfall (16 bps of proceeds) and the sim-vs-live entry gap (+26.08 bps, job #175) out of every filled trade; entry noise (+-300 bps) and MEV are not added; the raw simulator result is report-only. Primary exit lag 2 slots; still optimistic versus the measured live exit leak; the lag-0 cell is an upper bound.

Reported per cell and fail model: n entered, filled, miss, counted days with days positive, mean SOL per trade, CI90 (gate cluster bootstrap, 1,000 draws, seed 1, 5th-95th percentile of the mean), total, total ex-top-3, tp/sl/time-stop counts, tp rate among filled. For the primary also: per-day table, first-7 vs last-6 days (report-only), the primary without w1 (report-only), and the sharp-drop rate under the #336 label (report-only; it measures post-migration volatility, not rugs). Raw (unadjusted) cells are report-only.

## What each outcome would mean (declared in advance)

**Haircut (the deciding read).** The rules below are applied to the PRIMARY net of the measured live costs, as a post-processing adjustment of each filled trade's net0 (lamports, before the per-side fee), under both fail models:

- exit proceeds P = max(net0 + size, 0) (a sell that could not be quoted has no proceeds and is left alone);
- entry gap g = 26.08 bps (mean sim-vs-live entry gap, job #175; the sim got more tokens than live), a cut in tokens received on every filled entry, so proceeds scale by (1 - g);
- sell shortfall s = 16 bps of the sell proceeds (worst of a25eb17's -11..-16 bps), so proceeds scale by (1 - s);
- net0' = net0 - P * (1 - (1 - g)(1 - s)) = net0 - P * 0.0042038 (for g = 0.002608, s = 0.0016). Misses and censored rows are unchanged. The fee and both fail models are applied afterwards by the operating point's own pricing. MEV is not added; AMM concavity is ignored (proceeds scale linearly in tokens).

The unadjusted simulator result is reported too (`cells_raw`, "RAW" rows), report-only. Every cell in `cells` is haircut.

The bars are gate-shaped (not the gate). A leg "passes" on the haircut primary when all of these hold:

1. n entered >= 100;
2. at least 5 counted days with trades, with a majority of those days positive;
3. CI90 lower bound of the mean SOL per trade > 0;
4. total SOL ex-top-3 > 0.

Rules, applied in this order by the tool (first match wins). The tool computes the matched rule and any modifier and writes them into `report.json` and `report.md`:

0. **Precondition: a refused or aborted run** (V coverage over the limit, a primary trade on a no-V pool, an exception, a cancel): no reading. A refusal after rows were read logs all six cells to the tries logs with status `refused_after_read` (an exception or cancel after rows start: `aborted_after_read`) and writes no `report.json`.
1. **n entered < 100:** inconclusive. No statement about the size step either way.
2. **Either leg's mean < 0** (flat or pressure): strong caution against any size step. The in-pool result was likely winner's curse.
3. **Every bar passes under BOTH legs:** consistent with EXP-012 still working on older days at a 2-slot exit lag. Context only for DEC-020: no simulation can say whether 0.25 SOL works (DEC-020 §1), and Option A (after the 10-16 forward read) remains the recommendation. It does not authorise any size and cannot be cited as evidence for EXP-012.
4. **Anything else** (for example pressure passes but flat fails, or a bar fails on either leg): does not support.

**Modifier (rule 5), applies to a rule-3 match only:** if the first-7 and last-6 halves disagree in sign on either leg, or the primary without w1's hours flips sign on either leg (a half or a without-w1 book with no trades counts as a disagreement), the reading is downgraded from rule 3 to "does not support". The modifier is recorded whether or not it fires.

**This is never a promote.** The only evidence for EXP-012 is the 10-16 forward read and the promotion gate on a fresh holdout. Any edge claim still goes through `quant-proof`.

## Honest limits

- Exploration pool, best-of-N: all six cells are logged under pool `explore-0814` (count starts at 1 for DEC-017 (a)) in `--tries-log` and the repo's `data/tries.jsonl`, including when a run stops after reading rows (status field).
- V map: `/data/mal/pumpswap-virtual/pool_v_0814.json`, sha256 `2506f7d2d8475e44ca70a8c536dbb7405930b1092edca331dbbe611236b4d2f8` (job #196: 34,945 pools, 1 null of the 16,346 August pools). The pre-pass and the no-V trade count still guard against that one null pool.

## Result (2026-10-06): refused_after_read, no reading

- **What happened.** Job #207 at c946a12 (#346) computed every cell's rows. Then the pre-declared refusal fired: **1 primary-cell trade** was on a pool the V adapter had priced without V.
  - Across the run, the adapter reported 30 no-V pools: 29 were absent from `pool_v_0814.json` and 1 was a closed account.
  - The pool-field pre-pass had passed under 1% missing prints, so this was the per-trade rule catching what the coverage rule allows.
- **Record.** 6 tries lines with `status: refused_after_read` are in `data/tries.jsonl`; the explore-0814 tries count is 1 + 6 = 7. No report was written. No one read any outcome. The scratch directory is sealed (0700) at `/data/mal/exp012-backcheck-0814.refused-job207`.
- **Reading.** Rule 0 says a refused run has no reading.
- **Not rerun.** A rerun would be a second set of tries on the same days. EXP-015's pre-registered bar 3 already compares frozen EXP-012, paired, on these days at the same costs, so little is lost.
- **Lesson carried forward.** The next V map (`pool_v_0909.json`, job #224) adds those 30 pools, so EXP-015's per-trade no-V rule doesn't refuse the same way.

