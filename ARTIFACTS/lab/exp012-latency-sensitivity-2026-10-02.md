# EXP-012 entry-latency sensitivity (exploration, not evidence), 2026-10-02

**Exploration only.** This reads the 9-day exploration pool EXP-012 was frozen on. It is not a holdout, not evidence for the gate, and not a promote. Every number below is copied from [exp012-latency/latency_sensitivity.md](exp012-latency/latency_sensitivity.md) (JSON alongside).

**Run details:**
- MiScusi job #54, code `claude/exp012-latency-sensitivity` (PR #224) at `45ea51b`.
- Wall time 4,363 s, no Helius credits.
- Inputs: the clean views `fast-pool-2026-09-18T23_2026-09-22T00`, `oracle-insample-2026-09-22_25` and `oracle-live-2026-09-25_27` (`--verify-view`).

## Question

The replay enters at the start of slot migration+1 (`ENTRY_LAND_K = 1`), ahead of every other transaction in that slot. A live entry from `mal-fast-0` lands later. The fast public tape's receive lag behind block time had p50 1.166 to 1.760 s in its quiet stretch and 1.28 to 7.152 s in the 10-01T17–19 window ([tape note](tape-coverage-chain-verdict-2026-10-02.md)). At about 3.7 slots/s that is about 4 to 6.5 slots when quiet, and up to about 26 slots in a bad window. Most of EXP-012's lift is fill selection (EXP-012 Result), so this is the assumption most likely to break live.

## Method

- **Selection:** the stored out-of-fold scores of the freeze's 9-fold LODO (`ARTIFACTS/exp012/oof_scores.json`). A row is selected iff its OOF score ≥ the frozen threshold `0.8030766588450794`. Of 8,801 rows, 881 are selected. The selection is not in-sample to the model that scored it, but it is the same 9 days the recipe was tuned on.
- **Latency:** the entry slot is moved to migration + k for k ∈ {1, 2, 4, 6, 8, 12}. Everything else is unchanged: exit, size 0.5 SOL, priority, both fail models.
- **Gate statistics:** from `tools.exp011_score.compute_gate` (1,000 draws, seed 1).

## Result

Mean SOL per trade (0.5 SOL entries), 90% CI, ex-top-3 total SOL:

| k | book | n | fill | flat mean | flat CI90 | press mean | press CI90 | press ex-top3 |
| --- | --- | ---: | ---: | ---: | --- | ---: | --- | ---: |
| 1 | OOF-selected | 881 | 0.978 | 0.03707 | [0.02577, 0.04845] | 0.02315 | [0.01543, 0.03088] | 19.039 |
| 1 | unfiltered | 8801 | 0.347 | 0.00067 | [-0.00159, 0.00281] | 0.00009 | [-0.00140, 0.00156] | -0.599 |
| 2 | OOF-selected | 881 | 0.958 | 0.03773 | [0.02642, 0.04920] | 0.02378 | [0.01684, 0.03096] | 19.843 |
| 4 | OOF-selected | 881 | 0.922 | 0.03068 | [0.01997, 0.04221] | 0.01875 | [0.01182, 0.02587] | 15.549 |
| 6 | OOF-selected | 881 | 0.910 | 0.02833 | [0.01726, 0.03909] | 0.01817 | [0.01132, 0.02503] | 15.113 |
| 8 | OOF-selected | 881 | 0.867 | 0.02322 | [0.01234, 0.03440] | 0.01387 | [0.00737, 0.02064] | 11.426 |
| 12 | OOF-selected | 881 | 0.826 | 0.02030 | [0.01005, 0.03046] | 0.01327 | [0.00614, 0.02034] | 10.897 |
| 12 | unfiltered | 8801 | 0.322 | -0.00049 | [-0.00249, 0.00146] | -0.00093 | [-0.00230, 0.00040] | -10.959 |

The unfiltered rows for k = 2, 4, 6 and 8 are in the generated report.

- **Selected book:** in this pool its pressure mean falls from 0.02315 (k = 1) to 0.01327 (k = 12). It stays above 0 with CI lower bound > 0 at every tested k. The tool reports no zero crossing through k = 12 and does not extrapolate.
- **Fill rate:** falls from 0.978 to 0.826.
- **Unfiltered book:** its mean is near 0 at every k, and its CI includes 0 except the k = 6 flat leg, whose lower bound is 0.00001.

## What this does and does not say

- **Says:** on exploration data, delaying the entry alone to the quiet-stretch p50 of the fast tape (about 4 to 6 slots) costs roughly 20% of the selected book's pressure mean (0.02315 → 0.01875 / 0.01817). It does not remove the edge on these days.
- **Does not say:**
  - Anything about live profit, or about hours outside the pool.
  - Anything about lags above 12 slots. The fast tape's bad-window p50 (up to about 26 slots) and its p90 are beyond the tested range.
  - Anything about the exit side: exits still fill at the frozen k = 1, so live decay is at least what is shown.
  - Anything about selection under delay: the selection score is computed at k = 1 timing.
- **Winner's curse still applies.** The recipe was tuned on these 9 days, and the EXP-012 holdout read showed returns fading toward its latest day.

## Use

This curve is the pre-check for DEC-016 Amendment 1 §5(a). The forward read's sensitivity re-score must use the **measured** fast-0 p50 and p90 at the time. A PASS that turns negative there does not support live.
