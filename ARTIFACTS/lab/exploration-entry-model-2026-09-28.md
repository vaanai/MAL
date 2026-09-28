---
cursor:
  subagentId: "exploration-entry-model-2026-09-28"
---

# Exploration: learned migrate entry filter (leave-one-day-out)

**Exploration, not a pre-registered test.** This is a candidate for a later pre-registered test, never a promote. Three UTC days is a small-sample warning by itself -- leave-one-day-out here means 3 folds, and the model picking rule and the feature list were chosen by looking at all three days' results together, so even the 'held-out' day numbers below carry some of the same winner's-curse risk as the exit-family scan in `ARTIFACTS/lab/exploration-exits-2026-09-28.md`. None of this clears the promotion gate (`ARTIFACTS/lab/migrate-direct-prereg.md`) and none of it is scored against forward paper.

## Data fence and reuse

Sealed fast-box backfill hours **2026-09-19T01 through 2026-09-21T23** (71 hours), the same fence as `tools/exploration_exits.py` (#139), enforced by the same whitelist. Entry execution (migrate trigger, slot+1, direct, 0.0005 SOL/side, 0.5 SOL) and the two exit rules (`tpsl_tp50_sl30`, `trail_30_act20`) are reused unchanged from that module -- only the entry SELECTION (which migrations to take) is new here.

## Features (knowable at decision time T, the migrate trigger)

Pre-migration curve stats are a running, causal accumulation over pump_bonding buy/sell events strictly before the migrate trigger's receive time -- see `compute_features` and `causal_events` in `tools/exploration_entry_model.py`, and the no-lookahead test in `tools/test_exploration_entry_model.py`, which shuffles extra events at or after the cutoff into the input and checks the features never move. `same_slot_buys` / `nearby_buy_sol` are the one deliberate exception: they reuse the frozen pressure-fail model's own input (`tools.latency_curve._pressure`), which looks at competing buys up to the order's own slot+1 landing -- the same causal boundary that model already uses and this repo has already reviewed. `creator_prior_mints_24h` is EXP-009's G1 definition, recomputed here for this pool only and left-censored at the pool start. `top_holder_share` nets buy/sell token amounts per wallet seen on the DEX tape; it has no visibility into wallet-to-wallet transfers, so it is a proxy, not a resolved cap table.

Feature list: `time_to_migrate_s`, `n_bonding_trades`, `n_buys`, `n_sells`, `n_buyers`, `n_sellers`, `buy_sol`, `sell_sol`, `net_flow_sol`, `buy_sell_ratio`, `sniper_buy_share`, `top_holder_share`, `price_return_pre`, `mcap_at_t_sol`, `same_slot_buys`, `nearby_buy_sol`, `hour_of_day`, `hour_sin`, `hour_cos`, `creator_prior_mints_24h`

## Settings (<=3, all reported; no other hyperparameter search)

- `lgb_shallow`: {"kind": "lightgbm", "num_leaves": 7, "min_data_in_leaf": 40, "learning_rate": 0.05, "rounds": 50}
- `lgb_medium`: {"kind": "lightgbm", "num_leaves": 15, "min_data_in_leaf": 20, "learning_rate": 0.05, "rounds": 100}
- `logreg_l2`: {"kind": "logreg", "C": 0.5}

Rows per exit (full pool, MISS included, no survivorship / DEC-007): `tpsl_tp50_sl30`=3029, `trail_30_act20`=3029

**Best setting by the mean(top10% press-net% - all press-net%) lift across both exits' 3 held-out days:** `lgb_medium` (mean lift +1.15 pct pts). This selection rule is fixed in code (`choose_best_setting`), not picked after seeing which one looked best.

## `tpsl_tp50_sl30`: held-out-day table, setting `lgb_medium`

| Held-out day | Cohort | n | Gross % | Flat net % | Pressure net % |
| --- | --- | ---: | ---: | ---: | ---: |
| 2026-09-19 | all | 962 | +1.16% | +0.19% | -0.05% |
| 2026-09-19 | top10 | 96 | +7.48% | +4.07% | +2.92% |
| 2026-09-19 | top20 | 192 | +5.68% | +2.54% | +1.47% |
| 2026-09-19 | top30 | 289 | +4.14% | +1.25% | +0.57% |
| 2026-09-20 | all | 1034 | +0.56% | -0.33% | -0.44% |
| 2026-09-20 | top10 | 103 | +5.69% | +2.56% | +1.03% |
| 2026-09-20 | top20 | 207 | +2.37% | -0.24% | -0.71% |
| 2026-09-20 | top30 | 310 | +1.74% | -0.77% | -1.07% |
| 2026-09-21 | all | 1033 | +0.52% | -0.38% | -0.12% |
| 2026-09-21 | top10 | 103 | +0.28% | -2.03% | -1.24% |
| 2026-09-21 | top20 | 207 | -1.08% | -3.16% | -1.76% |
| 2026-09-21 | top30 | 310 | +0.75% | -1.58% | -0.73% |

### `tpsl_tp50_sl30` top features, setting `lgb_medium` (mean gain/coef across the 3 folds)

| Feature | Mean importance | Days in its top-5 |
| --- | ---: | --- |
| `nearby_buy_sol` | 9.46e+03 | 2026-09-19, 2026-09-20, 2026-09-21 |
| `net_flow_sol` | 6.1e+03 | 2026-09-19, 2026-09-20, 2026-09-21 |
| `price_return_pre` | 5.55e+03 | 2026-09-19, 2026-09-20, 2026-09-21 |
| `mcap_at_t_sol` | 3.7e+03 | 2026-09-20, 2026-09-21 |
| `time_to_migrate_s` | 1.35e+03 | 2026-09-19, 2026-09-20 |
| `n_buys` | 1.04e+03 | - |
| `same_slot_buys` | 1.01e+03 | 2026-09-21 |
| `top_holder_share` | 891 | 2026-09-19 |

## `trail_30_act20`: held-out-day table, setting `lgb_medium`

| Held-out day | Cohort | n | Gross % | Flat net % | Pressure net % |
| --- | --- | ---: | ---: | ---: | ---: |
| 2026-09-19 | all | 962 | +4.51% | +3.00% | +1.37% |
| 2026-09-19 | top10 | 96 | +14.54% | +9.99% | +4.38% |
| 2026-09-19 | top20 | 192 | +10.08% | +6.22% | +3.33% |
| 2026-09-19 | top30 | 289 | +13.06% | +8.75% | +4.27% |
| 2026-09-20 | all | 1034 | +2.33% | +1.16% | +0.23% |
| 2026-09-20 | top10 | 103 | +0.25% | -2.04% | -2.36% |
| 2026-09-20 | top20 | 207 | -9.65% | -10.37% | -7.49% |
| 2026-09-20 | top30 | 310 | +7.98% | +4.49% | +1.34% |
| 2026-09-21 | all | 1033 | +1.61% | +0.54% | -0.11% |
| 2026-09-21 | top10 | 103 | +7.26% | +3.84% | +3.04% |
| 2026-09-21 | top20 | 207 | +0.06% | -2.19% | -0.89% |
| 2026-09-21 | top30 | 310 | +5.89% | +2.74% | -0.20% |

### `trail_30_act20` top features, setting `lgb_medium` (mean gain/coef across the 3 folds)

| Feature | Mean importance | Days in its top-5 |
| --- | ---: | --- |
| `price_return_pre` | 9.4e+03 | 2026-09-19, 2026-09-20, 2026-09-21 |
| `net_flow_sol` | 8.34e+03 | 2026-09-19, 2026-09-20 |
| `nearby_buy_sol` | 6.57e+03 | 2026-09-19, 2026-09-20, 2026-09-21 |
| `mcap_at_t_sol` | 2.14e+03 | 2026-09-20 |
| `time_to_migrate_s` | 2.06e+03 | 2026-09-19, 2026-09-21 |
| `same_slot_buys` | 1.21e+03 | 2026-09-20, 2026-09-21 |
| `top_holder_share` | 1.12e+03 | 2026-09-19 |
| `n_buyers` | 833 | 2026-09-21 |

## All settings, all exits (for comparison)

### `tpsl_tp50_sl30` / `lgb_shallow`

| Held-out day | n (all) | Pressure net %, all | Pressure net %, top10 | Pressure net %, top20 | Pressure net %, top30 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-19 | 962 | -0.05% | +0.65% | +1.57% | +0.73% |
| 2026-09-20 | 1034 | -0.44% | -1.87% | -1.10% | -0.57% |
| 2026-09-21 | 1033 | -0.12% | -1.88% | +0.73% | -0.53% |

### `tpsl_tp50_sl30` / `lgb_medium`

| Held-out day | n (all) | Pressure net %, all | Pressure net %, top10 | Pressure net %, top20 | Pressure net %, top30 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-19 | 962 | -0.05% | +2.92% | +1.47% | +0.57% |
| 2026-09-20 | 1034 | -0.44% | +1.03% | -0.71% | -1.07% |
| 2026-09-21 | 1033 | -0.12% | -1.24% | -1.76% | -0.73% |

### `tpsl_tp50_sl30` / `logreg_l2`

| Held-out day | n (all) | Pressure net %, all | Pressure net %, top10 | Pressure net %, top20 | Pressure net %, top30 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-19 | 962 | -0.05% | +3.78% | +1.15% | +0.57% |
| 2026-09-20 | 1034 | -0.44% | -5.75% | -1.38% | -1.24% |
| 2026-09-21 | 1033 | -0.12% | +0.92% | -1.81% | -0.16% |

### `trail_30_act20` / `lgb_shallow`

| Held-out day | n (all) | Pressure net %, all | Pressure net %, top10 | Pressure net %, top20 | Pressure net %, top30 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-19 | 962 | +1.37% | -1.11% | +0.82% | +3.81% |
| 2026-09-20 | 1034 | +0.23% | -2.95% | +5.89% | +1.20% |
| 2026-09-21 | 1033 | -0.11% | +0.86% | -1.12% | -1.57% |

### `trail_30_act20` / `lgb_medium`

| Held-out day | n (all) | Pressure net %, all | Pressure net %, top10 | Pressure net %, top20 | Pressure net %, top30 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-19 | 962 | +1.37% | +4.38% | +3.33% | +4.27% |
| 2026-09-20 | 1034 | +0.23% | -2.36% | -7.49% | +1.34% |
| 2026-09-21 | 1033 | -0.11% | +3.04% | -0.89% | -0.20% |

### `trail_30_act20` / `logreg_l2`

| Held-out day | n (all) | Pressure net %, all | Pressure net %, top10 | Pressure net %, top20 | Pressure net %, top30 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-19 | 962 | +1.37% | -4.73% | +1.01% | +4.50% |
| 2026-09-20 | 1034 | +0.23% | -3.31% | -6.68% | +0.90% |
| 2026-09-21 | 1033 | -0.11% | -2.53% | -0.53% | -1.43% |

Wall time: 839s. Full grid: `exploration-entry-model-2026-09-28.json`.

