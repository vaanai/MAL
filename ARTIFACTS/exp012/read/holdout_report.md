VERDICT: PASS

# EXP-012 holdout report (2026-10-01T16:13:58Z)

threshold=0.8030766588450794 n_holdout_rows=5547 n_entered=451 selected_fraction_overall=0.08130521002343609

gate: n=451 promote=True blockers=[]
flat: mean_sol=0.03486573251662971 total_sol=15.724445365 total_ex_top3_sol=14.039689244
pressure_scale_1: mean_sol=0.020540998002217297 total_sol=9.263990099 total_ex_top3_sol=8.216918514 promote=True

## Per-day
| day | n_test | n_entered | selected_fraction | flat_mean_pct | press_mean_pct |
| --- | --- | --- | --- | --- | --- |
| 2026-09-03 | 463 | 32 | 0.06911447084233262 | 14.3655608915625 | 8.510359764942597 |
| 2026-09-04 | 941 | 90 | 0.09564293304994687 | 12.622849517222223 | 7.657262850948587 |
| 2026-09-05 | 887 | 70 | 0.07891770011273957 | 2.6551617290000005 | 1.7706783254519365 |
| 2026-09-06 | 860 | 77 | 0.08953488372093023 | 7.291442405584416 | 3.1982407030396973 |
| 2026-09-07 | 991 | 89 | 0.08980827447023208 | 7.54923623775281 | 4.272681797221064 |
| 2026-09-08 | 929 | 68 | 0.07319698600645856 | 3.2488412224999994 | 2.3079518281574627 |
| 2026-09-09 | 476 | 25 | 0.052521008403361345 | -3.6388378304 | -0.6443428033483387 |

## Fill-conditional / baseline
- entered_all: n=451 flat_mean_pct=6.973146505254989 press_mean_pct=4.108199601248119
- entered_filled_only: n=446 flat_mean_pct=7.052441869663677 press_mean_pct=4.15537672682265
- all_filled_baseline: n=1555 flat_mean_pct=5.436210969446945 press_mean_pct=2.443506369037635
- all_attempts_baseline_no_filter: n=5547 flat_mean_pct=1.4519754926068142 press_mean_pct=0.613025491951239

holdout is a single fast-box block walked by three walkers (w1/w2/w3) and read from their deduplicated copies; no cross-source split is possible, only within-block day variation

## Dedupe counts (outcome-blind, from the manifests)
- w1 creates: rows_in=57498 rows_out=57498 removed=0 hours_with_duplicates=0
- w1 migrations: rows_in=2407 rows_out=2407 removed=0 hours_with_duplicates=0
- w1 trades: rows_in=69144507 rows_out=69144507 removed=0 hours_with_duplicates=0
- w2 creates: rows_in=45354 rows_out=45354 removed=0 hours_with_duplicates=0
- w2 migrations: rows_in=2082 rows_out=2082 removed=0 hours_with_duplicates=0
- w2 trades: rows_in=50710531 rows_out=50710531 removed=0 hours_with_duplicates=0
- w3 creates: rows_in=57862 rows_out=57862 removed=0 hours_with_duplicates=0
- w3 migrations: rows_in=2295 rows_out=2295 removed=0 hours_with_duplicates=0
- w3 trades: rows_in=49207277 rows_out=49207277 removed=0 hours_with_duplicates=0
