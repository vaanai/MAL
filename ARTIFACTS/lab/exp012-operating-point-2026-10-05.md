# EXP-012 operating point under V pricing, paired vs the frozen point (exploration, best-of-N, not evidence), 2026-10-05

**Exploration, best-of-N, not a promote, not gate evidence.** TEMPLATE: the manager fills the `<...>` fields after the run, copying numbers from `exp012-operating-point/operating_point.md` (JSON alongside). Do not round up. Delete this line when filled.

This reads the 9-day exploration pool EXP-012 was frozen on. It never touches the EXP-012 holdout, the backup block, the EXP-011 block or the forward walk (2026-10-02 onwards).

**Run details:**
- MiScusi job `<id>`, code `<sha>` (`tools/exp012_operating_point.py`, PR #323). Wall time `<s>`; no Helius credits.
- V map `/data/mal/pumpswap-virtual/pool_v.json` (sha256 `<..>`). Adapter counts `<prints / corrected / no V>`. Result JSON sha256 `<..>`.
- Cells computed and logged: **N = `<36>`** (12 primary, 12 fee sensitivity, 12 size sensitivity). Cumulative tries on these 9 days: 18 existing + `<36>` new = `<54>`. Tries log `/data/mal/ops/tries-exp012-opoint.jsonl`. The first (cancelled, job #173) run appended no lines: the log is written only after the pass and the file did not exist.

## Question

With the live probe's measured costs (build a25eb17; per-side fee 505,000 lamports), does a higher entry threshold or a later entry k change SOL per trade relative to the frozen point (threshold 0.8031, k=6, 0.05 SOL, fee 505k)?

Primary grid at 0.05 SOL, 505k: thresholds {0.8031, 0.82, 0.85} x k {6, 8, 10, 12}. Below 0.803 already failed DEC-017 (c) (pressure -0.00312, CI90 [-0.01022, +0.00426]); 0.90 keeps 4 mints. The tool's k counts from the first PumpSwap print; live a25eb17 entries land k(migrate) = 5-6 (notebook evidence, jobs #170/#171). No drift rule (removed: look-ahead, and it turned fee-paid misses into free skips). Size is never selected across.

## Frozen point and primary grid

`<table: n, trades/day, flat and pressure mean, CI90, SOL/day, ex-top3, days positive>`

## Nested leave-one-day-out over the threshold, per fixed k, paired vs frozen

Pick the threshold on 8 days (pooled pressure paired mean, training floor 100 entered trades, unavailable folds not defaulted), score on the 9th. Paired on the frozen threshold's mints; a skipped mint scores 0. UPPER BOUND: the freeze stored no inner OOF scores.

| k | frozen level: press mean (CI90) | held-out paired press (CI90) | held-out paired flat (CI90) | held-out days+ (press / flat) | unavailable folds | optimism gap |
| ---: | --- | --- | --- | --- | ---: | ---: |
| 6 | `<>` | `<>` | `<>` | `<>/9` `<>/9` | `<>` | `<>` |
| 8 | `<>` | `<>` | `<>` | `<>` | `<>` | `<>` |
| 10 | `<>` | `<>` | `<>` | `<>` | `<>` | `<>` |
| 12 | `<>` | `<>` | `<>` | `<>` | `<>` | `<>` |

## Fee 155k sensitivity (same cells)

`<table>`. Fee and k are coupled live (DEC-019 Am.1 stop rule); the pressure curve is calibrated at 500k.

## Size sensitivity (frozen threshold, not a selection)

**Missing size-proportional costs (exit lag, sell shortfall, MEV); does not support any live size above 0.05 SOL.**

`<table>`

## What this does and does not say

- `<Does any threshold or k beat the frozen point on the nested paired estimate, with CI90 above 0? Quote, do not extrapolate.>`
- Exit delay stays at the frozen k=1 'start'; the probe's sell shortfall (-11 to -16 bps) and +-300 bps entry noise are not added.
- Not the promotion gate. Any operating-point change has to be pre-registered before the EXP-012 forward read (DEC-016).

## Decision

`<none, or what to carry into the live-trial pre-registration>`
