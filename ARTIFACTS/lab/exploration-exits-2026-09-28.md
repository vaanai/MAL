---
cursor:
  subagentId: "exploration-exits-2026-09-28"
---

# Exploration: exit families on migrate (exploration pool only)

**Exploration, not a pre-registered test.** These cells were picked by scanning 41 exit rules against the same fixed entry, on the exploration pool only. The best cell here is the best of many by construction -- winner's curse applies. None of this clears the promotion gate (ARTIFACTS/lab/migrate-direct-prereg.md), and none of it is scored against the selection window, the out-of-sample window, or forward paper. A candidate from this file needs its own pre-registration and its own out-of-sample test before it means anything.

## Data fence

Sealed fast-box backfill hours **2026-09-19T01 through 2026-09-21T23** in `/var/lib/mal/backfill-fast` (read-only), 71 hours, enforced by an explicit whitelist and an assertion in `tools/exploration_exits.py`. No hour older than 2026-09-19T01 (EXP-009's holdout) and no forward-paper output was read. No local Oracle in-sample copy was found on this box, so the pool is fast-box only.

Hours read (71): `2026-09-19T01, 2026-09-19T02, 2026-09-19T03, 2026-09-19T04, 2026-09-19T05, 2026-09-19T06, 2026-09-19T07, 2026-09-19T08, 2026-09-19T09, 2026-09-19T10, 2026-09-19T11, 2026-09-19T12, 2026-09-19T13, 2026-09-19T14, 2026-09-19T15, 2026-09-19T16, 2026-09-19T17, 2026-09-19T18, 2026-09-19T19, 2026-09-19T20, 2026-09-19T21, 2026-09-19T22, 2026-09-19T23, 2026-09-20T00, 2026-09-20T01, 2026-09-20T02, 2026-09-20T03, 2026-09-20T04, 2026-09-20T05, 2026-09-20T06, 2026-09-20T07, 2026-09-20T08, 2026-09-20T09, 2026-09-20T10, 2026-09-20T11, 2026-09-20T12, 2026-09-20T13, 2026-09-20T14, 2026-09-20T15, 2026-09-20T16, 2026-09-20T17, 2026-09-20T18, 2026-09-20T19, 2026-09-20T20, 2026-09-20T21, 2026-09-20T22, 2026-09-20T23, 2026-09-21T00, 2026-09-21T01, 2026-09-21T02, 2026-09-21T03, 2026-09-21T04, 2026-09-21T05, 2026-09-21T06, 2026-09-21T07, 2026-09-21T08, 2026-09-21T09, 2026-09-21T10, 2026-09-21T11, 2026-09-21T12, 2026-09-21T13, 2026-09-21T14, 2026-09-21T15, 2026-09-21T16, 2026-09-21T17, 2026-09-21T18, 2026-09-21T19, 2026-09-21T20, 2026-09-21T21, 2026-09-21T22, 2026-09-21T23`

## Entry (fixed across every cell)

Migrate trigger, slot+1 start bound, direct route, priority 0.0005 SOL/side (500,000 lamports, the slot+1 p75 from `ARTIFACTS/lab/fee-audit-2026-09-27.md`), size 0.5 SOL. Fail models: flat 15% on sends, and the frozen pressure curve at scale 1 (intercept -1.4548727851312098, not refit on this pool). Reused unchanged from `tools/latency_curve.py` / `tools/migrate_direct_oos.py`.

## Cell count: 41

tp/sl grid 6x4=24, trailing stop 4 levels x {with, without +20% activation}=8, time caps (2/5/10/30/60 min) on tp50_sl30=5, partial-take ladder (sell half at +X, trail rest 20%) 4 take levels=4. Total 24+8+5+4=41.

Ranked by the **min over the 3 UTC days of the pressure-model net mean** -- not by pooled mean -- because per-day consistency, not the pooled average, is the thing a single lucky day can fake.

## Top 3 candidates

### 1. `trail_30_act20` -- trail 30% off running high, arms at +20%, 30 min cap

n=3010, fill rate=32.36%, gross mean=+2.85%, flat net=+1.60% (CI lo -0.87%), pressure net=+0.52% (CI lo -0.84%)

| Day | n | flat net % | pressure net % |
| --- | ---: | ---: | ---: |
| 2026-09-19 | 959 | +3.09% | +1.43% |
| 2026-09-20 | 1028 | +1.16% | +0.23% |
| 2026-09-21 | 1023 | +0.65% | -0.04% |

### 2. `trail_30` -- trail 30% off running high, 30 min cap

n=3010, fill rate=32.36%, gross mean=+2.19%, flat net=+1.04% (CI lo -1.07%), pressure net=+0.22% (CI lo -0.91%)

| Day | n | flat net % | pressure net % |
| --- | ---: | ---: | ---: |
| 2026-09-19 | 959 | +0.27% | -0.16% |
| 2026-09-20 | 1028 | +1.03% | +0.12% |
| 2026-09-21 | 1023 | +1.77% | +0.69% |

### 3. `trail_10_act20` -- trail 10% off running high, arms at +20%, 30 min cap

n=3010, fill rate=32.36%, gross mean=+2.40%, flat net=+1.22% (CI lo -0.69%), pressure net=+0.23% (CI lo -0.64%)

| Day | n | flat net % | pressure net % |
| --- | ---: | ---: | ---: |
| 2026-09-19 | 959 | +1.59% | +0.63% |
| 2026-09-20 | 1028 | +0.22% | -0.19% |
| 2026-09-21 | 1023 | +1.87% | +0.29% |

## Full ranking

| Rank | Cell | Family | n | Fill | Gross % | Flat net % | Pressure net % | Min-day pressure % |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `trail_30_act20` | trailing_stop | 3010 | 32.36% | +2.85% | +1.60% | +0.52% | -0.04% |
| 2 | `trail_30` | trailing_stop | 3010 | 32.36% | +2.19% | +1.04% | +0.22% | -0.16% |
| 3 | `trail_10_act20` | trailing_stop | 3010 | 32.36% | +2.40% | +1.22% | +0.23% | -0.19% |
| 4 | `tpsl_tp20_sl30` | tp_sl_grid | 3010 | 32.36% | +0.96% | +0.00% | -0.09% | -0.25% |
| 5 | `tpsl_tp35_sl40` | tp_sl_grid | 3009 | 32.34% | +0.88% | -0.06% | -0.10% | -0.28% |
| 6 | `tpsl_tp35_sl30` | tp_sl_grid | 3009 | 32.34% | +0.80% | -0.13% | -0.12% | -0.28% |
| 7 | `tpsl_tp50_sl40` | tp_sl_grid | 3009 | 32.34% | +1.01% | +0.05% | -0.07% | -0.30% |
| 8 | `tpsl_tp20_sl40` | tp_sl_grid | 3010 | 32.36% | +0.99% | +0.03% | -0.10% | -0.32% |
| 9 | `tpsl_tp20_sl20` | tp_sl_grid | 3010 | 32.36% | +0.84% | -0.10% | -0.15% | -0.34% |
| 10 | `trail_10` | trailing_stop | 3010 | 32.36% | +1.93% | +0.81% | +0.11% | -0.37% |
| 11 | `trail_20` | trailing_stop | 3010 | 32.36% | +1.38% | +0.36% | -0.05% | -0.38% |
| 12 | `trail_15` | trailing_stop | 3010 | 32.36% | +1.50% | +0.46% | +0.00% | -0.42% |
| 13 | `timecap_2m_tp50_sl30` | time_cap | 3010 | 32.36% | +0.83% | -0.10% | -0.12% | -0.46% |
| 14 | `tpsl_tp50_sl30` | tp_sl_grid | 3009 | 32.34% | +0.78% | -0.15% | -0.19% | -0.47% |
| 15 | `timecap_30m_tp50_sl30` | time_cap | 3009 | 32.34% | +0.78% | -0.15% | -0.19% | -0.47% |
| 16 | `timecap_60m_tp50_sl30` | time_cap | 3009 | 32.34% | +0.79% | -0.14% | -0.18% | -0.47% |
| 17 | `timecap_10m_tp50_sl30` | time_cap | 3010 | 32.36% | +0.78% | -0.15% | -0.18% | -0.49% |
| 18 | `timecap_5m_tp50_sl30` | time_cap | 3010 | 32.36% | +1.03% | +0.06% | -0.06% | -0.51% |
| 19 | `trail_15_act20` | trailing_stop | 3010 | 32.36% | +2.10% | +0.96% | +0.22% | -0.51% |
| 20 | `tpsl_tp35_sl20` | tp_sl_grid | 3010 | 32.36% | +0.61% | -0.29% | -0.25% | -0.55% |
| 21 | `tpsl_tp20_sl15` | tp_sl_grid | 3010 | 32.36% | +0.30% | -0.56% | -0.42% | -0.63% |
| 22 | `trail_20_act20` | trailing_stop | 3010 | 32.36% | +1.76% | +0.67% | +0.08% | -0.64% |
| 23 | `tpsl_tp200_sl15` | tp_sl_grid | 3010 | 32.36% | +0.66% | -0.25% | -0.42% | -0.66% |
| 24 | `ladder_take50_trail20` | partial_ladder | 3010 | 32.36% | +1.00% | +0.02% | -0.24% | -0.75% |
| 25 | `tpsl_tp35_sl15` | tp_sl_grid | 3010 | 32.36% | +0.30% | -0.55% | -0.41% | -0.82% |
| 26 | `tpsl_tp50_sl15` | tp_sl_grid | 3010 | 32.36% | +0.45% | -0.43% | -0.37% | -0.86% |
| 27 | `tpsl_tp200_sl30` | tp_sl_grid | 3009 | 32.34% | +0.33% | -0.53% | -0.65% | -0.88% |
| 28 | `tpsl_tp50_sl20` | tp_sl_grid | 3010 | 32.36% | +0.56% | -0.34% | -0.34% | -0.90% |
| 29 | `ladder_take200_trail20` | partial_ladder | 3010 | 32.36% | +0.96% | -0.00% | -0.51% | -0.91% |
| 30 | `tpsl_tp200_sl20` | tp_sl_grid | 3010 | 32.36% | +0.52% | -0.37% | -0.63% | -0.98% |
| 31 | `ladder_take150_trail20` | partial_ladder | 3010 | 32.36% | +1.02% | +0.05% | -0.43% | -0.99% |
| 32 | `tpsl_tp75_sl30` | tp_sl_grid | 3009 | 32.34% | -0.07% | -0.86% | -0.72% | -1.08% |
| 33 | `tpsl_tp75_sl15` | tp_sl_grid | 3010 | 32.36% | +0.35% | -0.51% | -0.44% | -1.11% |
| 34 | `tpsl_tp100_sl15` | tp_sl_grid | 3010 | 32.36% | +0.22% | -0.62% | -0.55% | -1.13% |
| 35 | `tpsl_tp75_sl40` | tp_sl_grid | 3009 | 32.34% | -0.16% | -0.93% | -0.80% | -1.20% |
| 36 | `ladder_take100_trail20` | partial_ladder | 3010 | 32.36% | +0.56% | -0.34% | -0.59% | -1.29% |
| 37 | `tpsl_tp75_sl20` | tp_sl_grid | 3010 | 32.36% | +0.22% | -0.62% | -0.58% | -1.32% |
| 38 | `tpsl_tp100_sl30` | tp_sl_grid | 3009 | 32.34% | -0.34% | -1.09% | -0.89% | -1.53% |
| 39 | `tpsl_tp100_sl20` | tp_sl_grid | 3010 | 32.36% | +0.10% | -0.72% | -0.72% | -1.53% |
| 40 | `tpsl_tp200_sl40` | tp_sl_grid | 3009 | 32.34% | -0.83% | -1.50% | -1.31% | -1.53% |
| 41 | `tpsl_tp100_sl40` | tp_sl_grid | 3009 | 32.34% | -0.69% | -1.38% | -1.11% | -2.00% |

Wall time: 586s. Full grid: `exploration-exits-2026-09-28.json`.


## Addendum (2026-09-28T20:15Z, manager): concentration check kills the trailing-exit candidate

A `quant-proof` audit found the trailing simulation mechanically sound: it uses the same exit fill, latency, fees, slippage and fail models as the frozen tp/sl code, with no look-ahead in the running high, and this tool's tp50_sl30 (−0.19% pressure) agrees with the frozen scorer's (−0.24%) on nearly the same hours. The audit did not finish the concentration check, so the manager reran it with this tool's unmodified `run_all` (3 workers, `nice 19`, 656 s wall), keeping per-trade rows for two cells. Bootstrap: 1,000 draws, seed 1, 5th percentile. Size 0.5 SOL, n = 3,029 attempts each, 978 fills, 0 censored.

| Cell | Leg | Mean/attempt | Total SOL | Ex-top-3 SOL | Ex-top-10 SOL | 90% CI lo | Top 3 trades (SOL) | Per day 09-19 / 09-20 / 09-21 |
|---|---|---|---|---|---|---|---|---|
| trail_30_act20 | flat | +1.535% | +23.245 | **−7.492** | −26.632 | −0.803% | 16.58, 9.68, 4.47 | +3.002% / +1.159% / +0.545% |
| trail_30_act20 | pressure | +0.477% | +7.217 | **−8.048** | −19.634 | −0.817% | 9.41, 3.10, 2.75 | +1.367% / +0.233% / −0.110% |
| tpsl_tp50_sl30 | flat | −0.183% | −2.767 | −4.457 | −7.479 | −0.887% | 0.63, 0.54, 0.52 | +0.187% / −0.333% / −0.376% |
| tpsl_tp50_sl30 | pressure | −0.209% | −3.164 | −4.426 | −6.654 | −0.675% | 0.48, 0.42, 0.35 | −0.053% / −0.444% / −0.119% |

The trailing cell's pooled positive comes from a handful of moonshot trades; one 0.5 SOL position returned +16.58 SOL (flat). Without the top 3 it is **more** negative than tp50_sl30, and the per-day means fall steadily. It fails the gate's ex-top-3 condition by about 8 SOL on the exploration pool. **Decision:** trail_30_act20 is not pre-registered as EXP-010, the fresh-block backfill for it is not bought, and the EXP-010 ledger reservation is released. The lesson: on migrate entries the typical trade loses; exits can only harvest rare tails, so the lever is entry selection (see `exploration-entry-model-2026-09-28.md`: pre-migration buy pressure and momentum were the stable features).
