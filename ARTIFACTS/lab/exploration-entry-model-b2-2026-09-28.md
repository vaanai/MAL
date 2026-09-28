---
cursor:
  subagentId: "exploration-entry-model-b2-2026-09-28"
---

# Exploration: entry model on 5.7 days (fast + Oracle live, pre-clean-clock)

**Exploration, not a pre-registered test.** This lane adds pool B (the Oracle live tape, 2026-09-25T07 through 2026-09-27T23, real receive times) to pool A (the fast-box backfill, 2026-09-19T01 through 2026-09-21T23, synthetic receive times) behind the exact reuse described in `tools/oracle_live_adapter.py` and `tools/exploration_entry_model_b2.py`. The frozen execution, features, and settings are unchanged from `ARTIFACTS/lab/exploration-entry-model-2026-09-28.md`. It is a candidate for a later pre-registered test on a fresh, never-read block -- never a promote, and it does not touch the forward runner, the promotion gate, or any live book. Results are reported for A and B separately as well as together; no dataset-source feature is ever fed to the model.

## Data

Pool A: sealed fast-box backfill, 2026-09-19T01 - 2026-09-21T23 (71 hours), reused unchanged. Pool B: Oracle live tape, 2026-09-25T07 - 2026-09-27T23 (65 hours) plus PumpPortal creates through 2026-09-27 and 2026-09-28 strictly before 2026-09-28T00:00:00Z (enforced in `tools/oracle_live_adapter.py` by an explicit hour/day whitelist and a hard per-row cutoff -- see `tools/test_oracle_live_adapter.py`). Not a confirmation holdout: the 'Oracle live tape, pre-clean-clock' row in `docs/HOLDOUT_LEDGER.md` marks it exploration pool only.

Rows scored (full pool, MISS included, no survivorship / DEC-007): `tpsl_tp50_sl30`: A=3029, B=2300, `trail_30_act20`: A=3029, B=2296

Pool B ordering diagnostic (`tools/oracle_live_adapter.iter_trade_rows_sorted`, sampled 3 hours): 2241318 rows read, 45567 were out of (slot, t_recv_ms, event_index) order in the on-disk file before the defensive re-sort (0 means the live listener's file order already matched).

## Settings run here (no new hyperparameter search)

- `lgb_medium`: {"kind": "lightgbm", "num_leaves": 15, "min_data_in_leaf": 20, "learning_rate": 0.05, "rounds": 100}
- `logreg_l2`: {"kind": "logreg", "C": 0.5}

`lgb_medium` is primary (the prior module's own winner by its fixed selection rule); `logreg_l2` is reported as a second opinion. `lgb_shallow` is skipped here to keep the 6-fold run inside the box's time budget -- it is unchanged and still reported in the original 3-day file.

## `tpsl_tp50_sl30`

### A only (3 days), setting `lgb_medium`

| Held-out day | Cohort | n | Flat net % | Pressure net % | Pressure net total SOL | Ex-top-3 SOL |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-19 | all | 962 | +0.19% | -0.05% | -0.2539 | -1.4246 |
| 2026-09-19 | top10 | 96 | +4.07% | +2.92% | +1.4005 | +0.3654 |
| 2026-09-19 | top20 | 192 | +2.54% | +1.47% | +1.4070 | +0.2363 |
| 2026-09-20 | all | 1034 | -0.33% | -0.44% | -2.2959 | -3.0124 |
| 2026-09-20 | top10 | 103 | +2.56% | +1.03% | +0.5329 | -0.1059 |
| 2026-09-20 | top20 | 207 | -0.24% | -0.71% | -0.7327 | -1.3743 |
| 2026-09-21 | all | 1033 | -0.38% | -0.12% | -0.6147 | -1.7054 |
| 2026-09-21 | top10 | 103 | -2.03% | -1.24% | -0.6401 | -1.5212 |
| 2026-09-21 | top20 | 207 | -3.16% | -1.76% | -1.8183 | -2.7459 |

### B only (3 days), setting `lgb_medium`

| Held-out day | Cohort | n | Flat net % | Pressure net % | Pressure net total SOL | Ex-top-3 SOL |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-25 | all | 559 | -1.25% | -0.44% | -1.2397 | -2.2896 |
| 2026-09-25 | top10 | 56 | +5.96% | +3.24% | +0.9070 | +0.2987 |
| 2026-09-25 | top20 | 112 | +7.22% | +4.73% | +2.6486 | +1.9490 |
| 2026-09-26 | all | 901 | +0.86% | +0.44% | +1.9947 | +0.9372 |
| 2026-09-26 | top10 | 90 | +12.98% | +8.80% | +3.9589 | +3.2118 |
| 2026-09-26 | top20 | 180 | +7.87% | +4.64% | +4.1727 | +3.3249 |
| 2026-09-27 | all | 840 | +0.39% | +0.56% | +2.3330 | +1.0980 |
| 2026-09-27 | top10 | 84 | +6.38% | +4.46% | +1.8712 | +0.7681 |
| 2026-09-27 | top20 | 168 | +4.44% | +3.51% | +2.9447 | +1.8415 |

### A + B together (6 days), setting `lgb_medium`

| Held-out day | Cohort | n | Flat net % | Pressure net % | Pressure net total SOL | Ex-top-3 SOL |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-19 | all | 962 | +0.19% | -0.05% | -0.2539 | -1.4246 |
| 2026-09-19 | top10 | 96 | +1.14% | +0.55% | +0.2656 | -0.5521 |
| 2026-09-19 | top20 | 192 | +2.57% | +1.48% | +1.4248 | +0.3373 |
| 2026-09-20 | all | 1034 | -0.33% | -0.44% | -2.2959 | -3.0124 |
| 2026-09-20 | top10 | 103 | -1.13% | -1.22% | -0.6287 | -1.2383 |
| 2026-09-20 | top20 | 207 | -1.73% | -1.83% | -1.8979 | -2.5338 |
| 2026-09-21 | all | 1033 | -0.38% | -0.12% | -0.6147 | -1.7054 |
| 2026-09-21 | top10 | 103 | +1.19% | +1.53% | +0.7874 | -0.0685 |
| 2026-09-21 | top20 | 207 | -0.79% | -0.32% | -0.3286 | -1.2846 |
| 2026-09-25 | all | 559 | -1.25% | -0.44% | -1.2397 | -2.2896 |
| 2026-09-25 | top10 | 56 | +8.49% | +5.18% | +1.4512 | +0.8161 |
| 2026-09-25 | top20 | 112 | +0.80% | +0.99% | +0.5571 | -0.1675 |
| 2026-09-26 | all | 901 | +0.86% | +0.44% | +1.9947 | +0.9372 |
| 2026-09-26 | top10 | 90 | +10.99% | +7.14% | +3.2143 | +2.4686 |
| 2026-09-26 | top20 | 180 | +8.31% | +5.00% | +4.5008 | +3.6467 |
| 2026-09-27 | all | 840 | +0.39% | +0.56% | +2.3330 | +1.0980 |
| 2026-09-27 | top10 | 84 | +3.73% | +2.61% | +1.0955 | +0.0604 |
| 2026-09-27 | top20 | 168 | +1.53% | +1.70% | +1.4306 | +0.3542 |

Consistency over the 6 held-out days: top10 pressure-net beats all-trades pressure-net on **5/6** days; top10 pressure-net > 0 AND ex-top-3 SOL > 0 on **3/6** days.

### `tpsl_tp50_sl30` top features, setting `lgb_medium`, 6-day combined LODO

| Feature | Mean importance | Days in its top-5 |
| --- | ---: | --- |
| `price_return_pre` | 1.55e+04 | 2026-09-19, 2026-09-20, 2026-09-21, 2026-09-25, 2026-09-26, 2026-09-27 |
| `nearby_buy_sol` | 1.5e+04 | 2026-09-19, 2026-09-20, 2026-09-21, 2026-09-25, 2026-09-26, 2026-09-27 |
| `mcap_at_t_sol` | 1.16e+04 | 2026-09-19, 2026-09-20, 2026-09-21, 2026-09-25, 2026-09-26, 2026-09-27 |
| `time_to_migrate_s` | 2.84e+03 | 2026-09-19, 2026-09-20, 2026-09-21, 2026-09-25, 2026-09-26, 2026-09-27 |
| `same_slot_buys` | 2.77e+03 | 2026-09-19, 2026-09-20, 2026-09-21, 2026-09-25, 2026-09-26, 2026-09-27 |
| `net_flow_sol` | 1.54e+03 | - |
| `top_holder_share` | 1.06e+03 | - |
| `buy_sell_ratio` | 900 | - |

### `tpsl_tp50_sl30`, second opinion: `logreg_l2`, A + B together (6 days)

| Held-out day | Cohort | n | Flat net % | Pressure net % | Pressure net total SOL | Ex-top-3 SOL |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-19 | all | 962 | +0.19% | -0.05% | -0.2539 | -1.4246 |
| 2026-09-19 | top10 | 96 | -0.60% | +0.58% | +0.2784 | -0.7764 |
| 2026-09-19 | top20 | 192 | +2.12% | +1.49% | +1.4317 | +0.3768 |
| 2026-09-20 | all | 1034 | -0.33% | -0.44% | -2.2959 | -3.0124 |
| 2026-09-20 | top10 | 103 | -5.32% | -4.49% | -2.3148 | -2.9273 |
| 2026-09-20 | top20 | 207 | -0.70% | -0.87% | -0.8999 | -1.5124 |
| 2026-09-21 | all | 1033 | -0.38% | -0.12% | -0.6147 | -1.7054 |
| 2026-09-21 | top10 | 103 | -4.26% | -2.12% | -1.0924 | -2.1078 |
| 2026-09-21 | top20 | 207 | -0.09% | +0.58% | +0.5958 | -0.4689 |
| 2026-09-25 | all | 559 | -1.25% | -0.44% | -1.2397 | -2.2896 |
| 2026-09-25 | top10 | 56 | -1.63% | -3.29% | -0.9219 | -1.5730 |
| 2026-09-25 | top20 | 112 | +1.78% | +0.77% | +0.4318 | -0.4987 |
| 2026-09-26 | all | 901 | +0.86% | +0.44% | +1.9947 | +0.9372 |
| 2026-09-26 | top10 | 90 | +4.23% | +2.46% | +1.1067 | +0.3563 |
| 2026-09-26 | top20 | 180 | +5.36% | +3.94% | +3.5466 | +2.5490 |
| 2026-09-27 | all | 840 | +0.39% | +0.56% | +2.3330 | +1.0980 |
| 2026-09-27 | top10 | 84 | +9.05% | +6.63% | +2.7851 | +1.5501 |
| 2026-09-27 | top20 | 168 | +4.18% | +3.45% | +2.8989 | +1.6639 |

## `trail_30_act20`

### A only (3 days), setting `lgb_medium`

| Held-out day | Cohort | n | Flat net % | Pressure net % | Pressure net total SOL | Ex-top-3 SOL |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-19 | all | 962 | +3.00% | +1.37% | +6.5764 | +0.1855 |
| 2026-09-19 | top10 | 96 | +9.99% | +4.38% | +2.1039 | -1.9892 |
| 2026-09-19 | top20 | 192 | +6.22% | +3.33% | +3.2014 | -1.5989 |
| 2026-09-20 | all | 1034 | +1.16% | +0.23% | +1.2064 | -11.3221 |
| 2026-09-20 | top10 | 103 | -2.04% | -2.36% | -1.2160 | -2.9614 |
| 2026-09-20 | top20 | 207 | -10.37% | -7.49% | -7.7477 | -9.9523 |
| 2026-09-21 | all | 1033 | +0.54% | -0.11% | -0.5661 | -6.6474 |
| 2026-09-21 | top10 | 103 | +3.84% | +3.04% | +1.5657 | -2.3838 |
| 2026-09-21 | top20 | 207 | -2.19% | -0.89% | -0.9232 | -4.8728 |

### B only (3 days), setting `lgb_medium`

| Held-out day | Cohort | n | Flat net % | Pressure net % | Pressure net total SOL | Ex-top-3 SOL |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-25 | all | 559 | -2.75% | -1.38% | -3.8460 | -7.1116 |
| 2026-09-25 | top10 | 56 | +3.02% | +2.40% | +0.6733 | -1.8760 |
| 2026-09-25 | top20 | 112 | +0.67% | +0.34% | +0.1896 | -3.0760 |
| 2026-09-26 | all | 901 | +1.10% | +1.44% | +6.4944 | -9.2643 |
| 2026-09-26 | top10 | 90 | +5.20% | +3.78% | +1.6993 | -0.0476 |
| 2026-09-26 | top20 | 180 | +9.08% | +6.69% | +6.0245 | +0.2590 |
| 2026-09-27 | all | 836 | -1.17% | -1.07% | -4.4721 | -7.2053 |
| 2026-09-27 | top10 | 84 | -0.78% | -0.98% | -0.4102 | -1.9205 |
| 2026-09-27 | top20 | 167 | -2.37% | -1.47% | -1.2235 | -3.3701 |

### A + B together (6 days), setting `lgb_medium`

| Held-out day | Cohort | n | Flat net % | Pressure net % | Pressure net total SOL | Ex-top-3 SOL |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-19 | all | 962 | +3.00% | +1.37% | +6.5764 | +0.1855 |
| 2026-09-19 | top10 | 96 | +0.21% | -0.02% | -0.0108 | -1.0776 |
| 2026-09-19 | top20 | 192 | +4.30% | +1.47% | +1.4077 | -3.7993 |
| 2026-09-20 | all | 1034 | +1.16% | +0.23% | +1.2064 | -11.3221 |
| 2026-09-20 | top10 | 103 | -4.48% | -3.00% | -1.5427 | -3.2881 |
| 2026-09-20 | top20 | 207 | -10.38% | -7.17% | -7.4179 | -9.4504 |
| 2026-09-21 | all | 1033 | +0.54% | -0.11% | -0.5661 | -6.6474 |
| 2026-09-21 | top10 | 103 | -2.14% | -0.98% | -0.5070 | -2.7769 |
| 2026-09-21 | top20 | 207 | -0.39% | +0.07% | +0.0776 | -3.8720 |
| 2026-09-25 | all | 559 | -2.75% | -1.38% | -3.8460 | -7.1116 |
| 2026-09-25 | top10 | 56 | +7.19% | +5.84% | +1.6355 | -0.9138 |
| 2026-09-25 | top20 | 112 | +3.68% | +3.05% | +1.7053 | -1.5603 |
| 2026-09-26 | all | 901 | +1.10% | +1.44% | +6.4944 | -9.2643 |
| 2026-09-26 | top10 | 90 | +11.82% | +9.45% | +4.2526 | -1.3216 |
| 2026-09-26 | top20 | 180 | +4.63% | +3.44% | +3.0998 | -4.1893 |
| 2026-09-27 | all | 836 | -1.17% | -1.07% | -4.4721 | -7.2053 |
| 2026-09-27 | top10 | 84 | -1.62% | -0.82% | -0.3424 | -2.1583 |
| 2026-09-27 | top20 | 167 | -4.11% | -2.96% | -2.4746 | -4.6213 |

Consistency over the 6 held-out days: top10 pressure-net beats all-trades pressure-net on **3/6** days; top10 pressure-net > 0 AND ex-top-3 SOL > 0 on **0/6** days.

### `trail_30_act20` top features, setting `lgb_medium`, 6-day combined LODO

| Feature | Mean importance | Days in its top-5 |
| --- | ---: | --- |
| `price_return_pre` | 1.68e+04 | 2026-09-19, 2026-09-20, 2026-09-21, 2026-09-25, 2026-09-26, 2026-09-27 |
| `nearby_buy_sol` | 1.41e+04 | 2026-09-19, 2026-09-20, 2026-09-21, 2026-09-25, 2026-09-26, 2026-09-27 |
| `mcap_at_t_sol` | 1.22e+04 | 2026-09-19, 2026-09-20, 2026-09-21, 2026-09-25, 2026-09-26, 2026-09-27 |
| `same_slot_buys` | 2.45e+03 | 2026-09-19, 2026-09-20, 2026-09-21, 2026-09-25, 2026-09-26, 2026-09-27 |
| `time_to_migrate_s` | 2.16e+03 | 2026-09-19, 2026-09-20, 2026-09-21, 2026-09-25, 2026-09-26, 2026-09-27 |
| `net_flow_sol` | 1.54e+03 | - |
| `buy_sol` | 1.15e+03 | - |
| `n_bonding_trades` | 1.02e+03 | - |

### `trail_30_act20`, second opinion: `logreg_l2`, A + B together (6 days)

| Held-out day | Cohort | n | Flat net % | Pressure net % | Pressure net total SOL | Ex-top-3 SOL |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-19 | all | 962 | +3.00% | +1.37% | +6.5764 | +0.1855 |
| 2026-09-19 | top10 | 96 | -10.42% | -6.81% | -3.2685 | -4.9088 |
| 2026-09-19 | top20 | 192 | +8.67% | +4.47% | +4.2916 | -1.3255 |
| 2026-09-20 | all | 1034 | +1.16% | +0.23% | +1.2064 | -11.3221 |
| 2026-09-20 | top10 | 103 | -12.38% | -8.98% | -4.6232 | -7.7018 |
| 2026-09-20 | top20 | 207 | -6.19% | -4.59% | -4.7478 | -7.9156 |
| 2026-09-21 | all | 1033 | +0.54% | -0.11% | -0.5661 | -6.6474 |
| 2026-09-21 | top10 | 103 | -4.31% | -2.90% | -1.4958 | -3.5693 |
| 2026-09-21 | top20 | 207 | -4.11% | -2.48% | -2.5688 | -6.3031 |
| 2026-09-25 | all | 559 | -2.75% | -1.38% | -3.8460 | -7.1116 |
| 2026-09-25 | top10 | 56 | -4.88% | -4.86% | -1.3599 | -2.5915 |
| 2026-09-25 | top20 | 112 | -3.89% | -3.20% | -1.7922 | -3.7701 |
| 2026-09-26 | all | 901 | +1.10% | +1.44% | +6.4944 | -9.2643 |
| 2026-09-26 | top10 | 90 | +2.17% | +1.10% | +0.4941 | -0.8508 |
| 2026-09-26 | top20 | 180 | +14.39% | +11.02% | +9.9216 | -2.9791 |
| 2026-09-27 | all | 836 | -1.17% | -1.07% | -4.4721 | -7.2053 |
| 2026-09-27 | top10 | 84 | -2.25% | -1.68% | -0.7053 | -2.3356 |
| 2026-09-27 | top20 | 167 | -6.03% | -4.37% | -3.6458 | -5.6999 |

## Overall day-consistency (both exits, 6-day combined LODO, primary setting)

Across both exits' 6 held-out-day folds (12 fold-days total): top10 pressure-net beat all-trades pressure-net on **8/12**; top10 pressure-net > 0 with ex-top-3 SOL > 0 on **3/12**. This is the number that answers whether 3 more days changed the earlier module's inconsistent-lift finding -- read it next to `ARTIFACTS/lab/exploration-entry-model-2026-09-28.md`'s 3-day numbers, not instead of them.

Wall time: 1310s. Full grid: `exploration-entry-model-b2-2026-09-28.json`.

