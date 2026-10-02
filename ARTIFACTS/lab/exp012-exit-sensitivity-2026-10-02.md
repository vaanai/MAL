# EXP-012 exit-variant sensitivity (exploration, not evidence), 2026-10-02

**Exploration only.** This uses the 9-day exploration pool EXP-012 was frozen on, and EXP-012's out-of-fold selected entries (881 of 8,801 rows at the frozen threshold `0.8030766588450794`). Entry is the frozen k = 1 "start", 0.5 SOL. Only the exit changes.

- **Run:** MiScusi job #76, code `main` after #238. Rows provenance is in `rows_meta.json`.
- **Variants:** 17 tried. Every one is logged in [data/tries.jsonl](../../data/tries.jsonl) (17 lines).
- **Generated report:** [exp012-exit/exit_sensitivity.md](exp012-exit/exit_sensitivity.md), with JSON alongside. The numbers below are copied from it.

## Result: the DEC-017 exit-variant screen fails, so no exit-variant secondary is registered

DEC-017 §5 requires the **nested** leave-one-day-out estimate of the selection procedure's advantage over `tp50_sl30` to be > 0 under both fail models.

| scope | paired n | flat adv | flat CI90 | press adv | press CI90 | press days+ |
| --- | ---: | ---: | --- | ---: | --- | --- |
| all held-out days | 881 | -0.00640 | [-0.01103, -0.00185] | -0.00412 | [-0.00739, -0.00102] | 3/9 |
| fast days only (coarse, about 3 days) | 290 | 0.00025 | [-0.00247, 0.00275] | 0.00011 | [-0.00095, 0.00115] | 1/3 |

- **Nested choice:** choosing the exit on 8 days and scoring it on the 9th loses money against the frozen exit, with the CI wholly below 0 under both fail models.
- **Picks:** `timecap_10m_tp50_sl30` was picked on 6 of 9 held-out days, `tpsl_tp50_sl40` on 2 and `tpsl_tp100_sl30` on 1.
- **Paired increment:** no variant's paired increment against `tp50_sl30` has a 90% CI lower bound > 0 under either fail model. The best is `timecap_10m_tp50_sl30`: flat +0.00129 [-0.00061, 0.00330], pressure +0.00094 [-0.00013, 0.00213].
- **Trailing and ladder exits** (`trail_20`, `trail_30`, `ladder_*`) are clearly worse. Their paired CIs lie wholly below 0 under both fail models.

## The frozen exit holds up

`tpsl_tp50_sl30` ranks 2 of 17 by pressure mean:
- flat 0.03707 [0.02577, 0.04845], pressure 0.02315 [0.01543, 0.03088];
- pressure positive on 9 of 9 days.

These are the same numbers as the k = 1 row of the [latency note](exp012-latency-sensitivity-2026-10-02.md), so the two tools agree on the reference.

## Not covered

- **Exit-side latency:** not implemented. `tools/exploration_exits.py` has no delayed-exit fill, so live exits are untested against delay.
- **Not implemented, so not tried:** `tp30` and `trail_40`.
- **Winner's curse:** with 17 variants on the freeze days, the pooled ranking is not evidence. The nested estimate is the honest number, and it is negative.

## Consequence

The exit-variant candidate (DEC-017 (b)) is dropped. The remaining DEC-017 candidates are the expanded-pool refit (job #75 and successors) and possibly a looser threshold.
