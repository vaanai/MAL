# EXP-012 back-check on explore-0814 (pre-declaration, exploration only)

Written before any run. Tool: `tools/exp012_backcheck.py`. Not a promote, not gate evidence, never a confirmation holdout.

## Question

The frozen EXP-012 entry model (threshold 0.8031, never refit) was frozen on a 9-day exploration pool. What does that frozen strategy earn at the **live operating point** on 14 days of tape the model never saw, and whose migrate-entry outcomes nobody has read? The answer informs the live probe's size step ([DEC-020](../DEC/DEC-020-size-step-proposal.md)) and nothing else.

## Data

The exploration expansion `explore-0814`, `[2026-08-14T12, 2026-08-28T12)` (14 days, 336 h), clean views `/data/mal/clean-view/explore-0814/w1..w7`, each verified against its `VIEW.sha256`. The first 24 h (to 2026-08-15T12) are feature buffer only: migrations before 2026-08-15T12 are scored but not counted, so `creator_prior_mints_24h` is complete. Counted days: 2026-08-15T12 to 2026-08-28T12 (13 days; first and last UTC days are partial). The table is built by the frozen code path (`exp012_score.load_rows`, anchored chunk plan, `BUFFER_HOURS=24`, `MAX_HOME_HOURS=12`) and scored with the **frozen** model, not out-of-fold scores.

## Cells (6, declared here and in the module docstring; the test asserts they match the code)

Frozen threshold 0.8031, fee 505,000 lamports per side, V pricing, buy at k slots after migration with the 15% cap (a MISS pays the fee), tp50_sl30, 30-minute exit cap. Both fail models: flat 15% and pressure at slope scale 1.

| Role | k | Size (SOL) | Exit lag |
| --- | --- | --- | --- |
| **PRIMARY** | 6 | 0.05 | 0 |
| sensitivity | 6 | 0.25 | 0 |
| sensitivity | 6 | 0.5 | 0 |
| sensitivity | 4 | 0.05 | 0 |
| sensitivity | 8 | 0.05 | 0 |
| sensitivity | 6 | 0.25 | 2 slots (`exit_land_k=2`, exposed by `score_one`) |

Sensitivities are report-only. They are never selected among and cannot replace the primary. Sizes above 0.05 SOL do not model size-proportional costs (sell shortfall, MEV, slower exits), so they cannot by themselves support a larger live size.

Reported per cell and fail model: n entered, filled, miss, UTC days, days positive, mean SOL per trade, CI90 (gate cluster bootstrap, 1,000 draws, seed 1), total, total ex-top-3, tp/sl/time-stop counts, tp rate among filled. For the primary also: per-day table, first-7-days vs last-6-days split (report-only), and the sharp-drop rate under the #336 label (report-only; it measures post-migration volatility, not rugs).

## What each outcome would mean (declared in advance)

- **Primary pressure CI90 lower bound > 0 and flat mean > 0:** supports continuing the probe and supports a size-step proposal being considered. It does not clear the promotion gate and does not authorise any size.
- **Primary pressure mean > 0 but CI90 lower bound <= 0:** consistent with the in-pool picture (about break-even at 0.05 SOL). It is neither support nor caution for a size step; the fee-share argument in DEC-020 stands or falls on its own.
- **Primary pressure mean < 0 (either fail model):** a strong caution against sizing up. The in-pool result was likely winner's curse.
- **First-7 vs last-6 halves disagree in sign:** the result is unstable; read it as weaker than the pooled number either way.
- **V coverage incomplete (report flag):** the P&L is not V-priced for the uncovered pools and is not read at all until the V map covers them.
- Nothing here is a promote. Any edge claim still goes through `quant-proof` and the promotion gate on a fresh holdout.

## Honest limits

- The model never trained on these days and these outcomes were never read, but this is still an exploration pool: it is not a confirmation holdout, and the six cells are all logged (pool `explore-0814`, a count separate from the 9-day pool's).
- EXP-013 and EXP-014 planned screens on `explore-0814`. This read opens **migrate-entry outcomes** on those days. Their frozen screens are unchanged; they should record that this read happened.
- The live probe's sell shortfall (-11..-16 bps) and entry noise (+-300 bps) are not added.
- The pool -> V map (`/data/mal/pumpswap-virtual/pool_v.json`) was fetched 2026-10-04 and may not cover August pools. A pool with no V is left unpriced by V and counted; the report prints the count and flags the run if more than 1% of PumpSwap prints lack V.
