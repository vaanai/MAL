# EXP-012 back-check on explore-0814 (pre-declaration, exploration only)

Written before any run. Tool: `tools/exp012_backcheck.py`. Best-of-N context. Not a promote, not gate evidence, never a confirmation holdout.

## Question

The frozen EXP-012 entry model (threshold 0.8031, model md5 `a1810d219ed61db64a396f40dc302ce5`, never refit) was frozen on a 9-day exploration pool. What does that frozen strategy earn at the live operating point on 14 days the model never trained on? The answer is context for the live probe's size step ([DEC-020](../DEC/DEC-020-size-step-proposal.md)) and nothing else.

## What these days are NOT

They are **not unread**. `explore-0814/w1` `[2026-08-26T12, 2026-08-28T12)` was outcome-read by DEC-017 candidate (a) (`ARTIFACTS/lab/dec017-candidates-2026-10-02.md`, row "getblock_only (August w1)"; EXP-013 Amendment 5). The explore-0814 try count therefore starts at **1** and this run adds 6 (cumulative 7). After this run, the EXP-013/EXP-014 August bars are no longer on unread data; their frozen screens are unchanged but must record that this read happened. The report also gives the primary cell **without w1's hours**, report-only.

## Data

Clean views `/data/mal/clean-view/explore-0814/w1..w7`, each verified against its `VIEW.sha256`. The first 24 h (to 2026-08-15T12) are feature buffer only: migrations before 2026-08-15T12 are scored but not counted. Counted window: 2026-08-15T12 to 2026-08-28T12, **13 counted days**. A counted day is a 24 h window starting 12:00Z, labelled by its start date (UTC calendar dates would give 14 partial days; none is counted as a day). The table is built by the frozen code path (`exp012_score.load_rows`, anchored chunk plan, `BUFFER_HOURS=24`, `MAX_HOME_HOURS=12`) and scored by the **frozen** model, not out-of-fold scores.

Before any scoring or P&L, a pre-pass reads only the pool field of PumpSwap prints for the counted migrated mints and checks the V map. The run exits 2 if more than 1% of those prints have no V. A pool whose map value is null (account missing) is MISSING, never V-less and never V = 0; an explicit 0 is a real V = 0. The coverage line (prints, covered, missing, missing pools) goes to stderr and into the report. The model md5 is asserted before any row is read, and `git rev-parse HEAD` is recorded in the report.

## Cells (6; declared here and in the module docstring; a test asserts they match the code)

Frozen threshold 0.8031, fee 505,000 lamports per side, V pricing, tp50_sl30, 30-minute exit cap. **k counts from the first PumpSwap print, not from migration.** A MISS pays the fee. Both fail models: flat 15% and pressure at slope scale 1.

| Role | k | Size (SOL) | Exit lag | Label |
| --- | --- | --- | --- | --- |
| **PRIMARY** | 6 | 0.05 | 0 | |
| sensitivity | 6 | 0.25 | 0 | mechanical (fee arithmetic + modelled AMM impact), not evidence; cannot support any live size (DEC-020 §1) |
| sensitivity | 6 | 0.5 | 0 | same as above |
| sensitivity | 4 | 0.05 | 0 | |
| sensitivity | 8 | 0.05 | 0 | |
| sensitivity | 6 | 0.25 | 2 slots | size label as above; exit lag is an optimistic lower bound on exit cost vs the measured live exit leak |

Sensitivities are report-only, never selected among, and cannot replace the primary. The 0.25 and 0.5 cells are reported as percent of size. Sell shortfall (-11..-16 bps), entry noise (+-300 bps) and MEV are not added, and exit lag 0/2 is optimistic versus the live exit leak.

Reported per cell and fail model: n entered, filled, miss, counted days with days positive, mean SOL per trade, CI90 (gate cluster bootstrap, 1,000 draws, seed 1, 5th-95th percentile of the mean), total, total ex-top-3, tp/sl/time-stop counts, tp rate among filled. For the primary also: per-day table, first-7 vs last-6 days (report-only), the primary without w1 (report-only), and the sharp-drop rate under the #336 label (report-only; it measures post-migration volatility, not rugs).

## What each outcome would mean (declared in advance)

The bars are gate-shaped. "Pass" below means all of these, **under both fail models** (flat 15% and pressure), on the primary cell:

1. n entered >= 100;
2. at least 5 counted days, with a majority of days positive;
3. CI90 lower bound of the mean SOL per trade > 0;
4. total SOL ex-top-3 > 0.

Outcomes:

- **Pass under both fail models:** supports continuing the probe and supports a size-step proposal being considered. It does not authorise any size, and it cannot be cited as evidence for EXP-012.
- **n < 100:** inconclusive. No statement about the size step either way; the CI is not read.
- **Pressure passes, but the flat lower bound <= 0 (or any other bar fails under flat):** not support. Report as mixed; the size-step case rests on DEC-020's own arithmetic, not on this run.
- **Mean >= 0 under both models but a bar other than n fails (CI lower bound <= 0, fewer than 5 days or a minority positive, or ex-top-3 <= 0):** consistent with about break-even at 0.05 SOL. Neither support nor caution.
- **Mean < 0 (either fail model):** a strong caution against sizing up. The in-pool result was likely winner's curse.
- **First-7 and last-6 halves disagree in sign, or the primary without w1 differs in sign from the full primary:** the result is unstable and is read as weaker than the pooled number either way.
- **V coverage over the limit:** the run refuses (exit 2) and nothing is read.

**This is never a promote.** The only evidence for EXP-012 is the 10-16 forward read and the promotion gate on a fresh holdout. Any edge claim still goes through `quant-proof`.

## Honest limits

- Exploration pool, best-of-N: all six cells are logged under pool `explore-0814` (count starts at 1 for DEC-017 (a)) in `--tries-log` and the repo's `data/tries.jsonl`.
- The V map `/data/mal/pumpswap-virtual/pool_v.json` was fetched 2026-10-04 and may not cover August pools; that is what the pre-pass checks.
