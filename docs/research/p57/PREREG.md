# P57 pre-registration: statistical take/skip veto (meta-label) on the frozen H5/Q50 BOOST-floor rule

Written before any fit. No model was fit and no feature was joined to a P&L column before this file was committed. Library row: lib-a-A39 (meta-labeling and conformal abstention). Exploration only; a pass is a hypothesis, never a promote, and no book has cleared the promotion gate. This is a statistical veto, not an LLM (the Haiku v3 veto was about placebo).

## Population and seals (exploration blocks only)

- Same 2,826 frozen binding trades as P55 (`/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet`): 1,702 discovery (explore-0814, 2026-08-14..08-28) and 1,124 confirmation (fresh-0903, exp011-0909, fast-pool-0918, oracle-insample-0922, to 2026-09-25T06). All are exploration-pool blocks in docs/HOLDOUT_LEDGER.md. No October hour, no HV4/Q50 output, no forward, DBC, arena or meta-capture file, no FLOOR G1, LaunchLab or D1 is read.
- Honest status: the confirmation set is NOT a clean out-of-sample set. Earlier H5 loss-filter work (h5-lossfilter) and P55 already looked at outcomes on both sets. Here "confirmation" means train-on-August, score-September, never the reverse; it is not a gate read.
- P&L: `p55_price.py` (PR #986, unchanged) at exit lag 2 slots, column `pnl_base_el2` (lamports, per 0.1 SOL stake, incl. 2 x 55,000 priority). Validated by P55 to match stored pnl exactly at the frozen lag. Net per trade is then scored under the flat 15% fail model (0.85*pnl + 0.15*(-55,000)) and the pressure-fail model at slope 1 (per-trade `p_fail`). Units: pp of the 0.1 SOL stake.

## Features (causal at entry only)

Source: `features_decision_time.parquet` built by `s02b_features.py` from path rows 0..i only, i = the trigger print; entry lands 1.9 s later, so every feature is known before the fill. Outcome columns are not in that file.

Excluded: all `qa_*` (use later rows); `f_boost_spent_frozen_sol` and `f_trig_t_frozen_s` (the source marks them report-only: frozen BOOST identity looks 1,600 slots ahead, frozen sps uses later block times); `f_hour_utc`, `f_dow` (calendar, cannot transfer Aug to Sep); `f_mayhem` and `f_has_create` if constant on train; `f_q_post_trig` (equals frozen q_trig, at most 40, a selection variable). Included: every other `f_*` column of that file (about 85), covering trigger-print size and fractions, timing from pool start, drain and price path, causal BOOST detector state (`f_boost_*_causal`, `f_t_since_last_boost_s`, `f_boost_rem_over_q`), non-BOOST flow windows (5/15/30/60 s), seller concentration, bonding-phase holder concentration, creator history and completes in prior windows.

Sign and direction: LightGBM learns direction, so no feature sign is imposed. I checked the source definitions for orientation: `f_drain_from_first_sol` = (first pre-trade Q minus post-trigger Q)/1e9 (positive = drained); `f_dd_from_max` = 1 - px/max px (positive = below the peak); `f_boost_rem_causal_sol` = 17.585 minus causal BOOST spend (positive = vault left); `f_t_since_*` are seconds, larger = older. Booleans are cast to 0/1; NaN is passed as missing. The prior loss-filter report found feature lift that changes sign between Aug and Sep (big-loser lift 0.746 vs 1.165), a known risk this test faces directly.

## Model (fixed, no grid)

LightGBM regressor, L2, on the discovery-trade net result `pnl_base_el2/1e8*100` (pp of stake) winsorised at the train 1st and 99th percentile (train percentiles only). Hyperparameters: n_estimators=200, learning_rate=0.03, num_leaves=7, max_depth=3, min_child_samples=40, subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=5, random_state=1, n_jobs=2. The fit target is the fail-model-free pnl; fail models are applied afterwards at scoring.

## Split and abstention rule

- Train: discovery (Aug) only. Evaluate: confirmation (Sep) only. Never the reverse.
- One threshold: skip the 20% of entries with the lowest predicted pnl. The cut is the 20th percentile of day-grouped 5-fold out-of-fold train predictions (folds by `day`, seed 1), so the threshold is chosen on train only and not on in-sample fit. The final model is refit on all train and scores confirmation; a confirmation trade is KEPT if its predicted pnl is above that cut.
- No second threshold, no conformal refinement in this lane (conformal abstention is a later step only if this passes).

## Statistic and kill lines

improvement = mean net per KEPT confirmation trade minus mean net per ALL confirmation trades, in pp of stake, for each of the two fail models.

KILL if any of:
1. improvement < +1.0 pp under the flat model or under the pressure-fail model.
2. Improvement does not survive dropping the top 3 trades (the three largest-pnl confirmation trades removed from the full set and from the kept set) or the best day (the day contributing most to the improvement, i.e. the day whose skipped trades most reduce the sum of the skipped pnl; that whole day removed): fail if improvement after either drop is < +0.5 pp under either model.
3. Kept trades < 60% of all confirmation trades.
4. Label-shuffle placebo: same recipe (shuffle the train pnl labels, refit, same threshold method), 20 shuffles seeds 1..20. KILL if the mean placebo improvement is at least half the real improvement under either fail model.

Also reported, not kill lines: day-bootstrap 5th percentile of the improvement (1,000 draws, seed 1, resampling days), improvement on train out-of-fold, the share of skipped trades with pnl <= -50%, and the capacity line: kept trades per UTC day on confirmation x 0.1 SOL x mean net edge of kept trades, per fail model.

CONTINUE only if none of 1-4 hits. Even then the book is not promoted; the next step would be a forward-paper registration.
