# EXP-012 operating point under V pricing, paired vs the frozen point (exploration, best-of-N, not evidence), 2026-10-05

**Exploration, best-of-N, not a promote, not gate evidence.**
- **Data:** the 9-day exploration pool EXP-012 was frozen on. The EXP-012 holdout, the backup block, the EXP-011 block and the forward walk (2026-10-02 onwards) were never read.
- **Numbers:** copied from the generated report [exp012-operating-point/operating_point.md](exp012-operating-point/operating_point.md). The JSON is alongside it, sha256 `4646b1104079b06d3e759400814f88589541146c47bab99ea15571a5da8d9732`. Nothing is rounded up.

## Run

- MiScusi job #177, code `86acedb` (`tools/exp012_operating_point.py`, PR #323), wall time 2,950 s. No Helius credits.
- Cells computed and logged: **N = 36** (12 primary, 12 fee sensitivity, 12 size sensitivity).
- Cumulative tries on these 9 days: 18 existing + 36 new = 54. All 36 are appended to [data/tries.jsonl](../../data/tries.jsonl).
- The first run (job #173, cancelled after the quant-proof blockers B1–B3) appended no lines.

## Question

Given the costs the live probe measured (per-side fee 505,000 lamports), does a higher entry threshold change SOL per trade relative to the frozen point at the entry latency we land at? The frozen point is threshold 0.8031, k=6, 0.05 SOL, fee 505k.

- **Primary grid:** 0.05 SOL, fee 505k, thresholds {0.8031, 0.82, 0.85} × k {6, 8, 10, 12}.
- **Excluded thresholds:** below 0.803 already failed DEC-017 (c), with pressure −0.00312, CI90 [−0.01022, +0.00426]. 0.90 keeps only 4 mints.
- **k:** the tool counts k from the first PumpSwap print. Live a25eb17 entries land k(migrate) = 5–6, notebook evidence from jobs #170 and #171.
- **Not in the grid:** no drift rule, and size is never selected across.

## Frozen point

| thr | k | size | fee | n | trades/day | flat mean | flat CI90 | press mean | press CI90 | press days+ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- | ---: |
| 0.8031 | 6 | 0.05 | 505000 | 881 | 97.89 | 0.00107 | [-0.00004, 0.00221] | 0.00058 | [-0.00012, 0.00131] | 7/9 |

## Nested leave-one-day-out over the threshold, per fixed k, paired vs frozen

**Method:**
- Pick the threshold on 8 days by pooled pressure paired mean, then score it on the 9th.
- Training floor: 100 entered trades. Unavailable folds are not defaulted.
- Pairing uses the frozen threshold's mints; a skipped mint scores 0.
- **UPPER BOUND:** the freeze stored no inner OOF scores.

| k | frozen level: press mean (CI90) | held-out paired press (CI90) | held-out paired flat (CI90) | held-out days+ (press / flat) | unavailable folds | optimism gap |
| ---: | --- | --- | --- | --- | ---: | ---: |
| 6 | 0.00058 [-0.00012, 0.00131] | -0.00035 [-0.00076, 0.00007] | -0.00072 [-0.00137, -0.00007] | 1/9, 0/9 | 0 | 0.00040 |
| 8 | 0.00016 [-0.00050, 0.00080] | -0.00009 [-0.00051, 0.00036] | -0.00027 [-0.00098, 0.00050] | 3/9, 3/9 | 0 | 0.00024 |
| 10 | -0.00009 [-0.00081, 0.00063] | 0.00028 [-0.00020, 0.00081] | 0.00035 [-0.00040, 0.00115] | 6/9, 6/9 | 0 | 0.00014 |
| 12 | -0.00029 [-0.00103, 0.00042] | 0.00069 [0.00018, 0.00128] | 0.00071 [-0.00005, 0.00156] | 8/9, 7/9 | 0 | -0.00000 |

## Fee and size sensitivity

These sections are reported only. They are not selections.
- **Fee:** the 155,000-per-side rows are in the generated report.
  - At the frozen threshold and k=6, the pressure mean goes from 0.00058 at 505k to 0.00110, CI90 [0.00040, 0.00182].
  - Fee and k are coupled live (DEC-019 Am.1), and the pressure curve is calibrated at 500k.
- **Size:** the rows at the frozen threshold are in the generated report. **They are missing size-proportional costs (exit lag, sell shortfall, MEV), so they do not support any live size above 0.05 SOL.**

## Reading

1. **At the k we land at (≈6), moving off the frozen threshold does not help.**
   - Choosing the threshold on 8 days and scoring the 9th loses against frozen: pressure −0.00035, CI90 [−0.00076, 0.00007], 1/9 days positive. Flat is −0.00072, with a CI wholly below 0.
   - Keep 0.8031.
2. **A higher threshold (0.85) only helps when entry is late** (k=12: pressure +0.00069, CI90 [0.00018, 0.00128], 8/9 days). That is not where the live probe lands now. It is an UPPER BOUND, and it is best-of-N.
3. **At 0.05 SOL with the 505k fee, the frozen point's pressure mean is 0.00058 SOL per trade, and its CI90 lower bound is just below 0.** That is roughly break-even after fees, which matches what the live probe is showing.
   - The 155k fee row at the same cell has a CI above 0. The fee drag is a probe-size effect: fees are about 2% of a 0.05 SOL trade and about 0.2% of a 0.5 SOL trade.
   - Whether the edge pays at larger size can't be answered here. It needs the EXP-012 forward read (DEC-016) and a live step at the larger size that measures the size-proportional costs.

## Caveats

- Only the ENTRY slot is delayed per k. The exit delay stays at the frozen k=1 "start".
- The live probe's sell shortfall (−11 to −16 bps) and its entry noise (±300 bps) are not added.
- Selection uses the stored OOF scores at k=1 timing for every k. The frozen features are pre-migration only, so the score is available at decision time for any k.
- These are the same 9 days the model was frozen on. Winner's curse applies.
