# P80 pre-registration: learned rug / big-loser veto (top 15% risk) on the frozen H5 binding book

Written 2026-10-27T14:0xZ, before any feature, label, score or P&L was computed. Library rows: drain-predictor-1..3 (features), hunt23-b-flow-V1 / P77 (prior veto attempt, NOT_DECIDABLE at 89% veto share). Paper only. Exploration blocks only. A pass is a LEAD toward an H5/Q50 amendment; it never changes a running book. No book has cleared the promotion gate. The dates are read by at least 14 families (HOLDOUT_LEDGER m >= 14): every number here is in-sample for the lab.

## Populations (P55/P77 code and paths, unchanged)
- Test population: `/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet`, 2,826 trades: 1,702 discovery (`frozen_disc`, explore-0814) and 1,124 confirmation (`frozen_conf`). Prints/meta: `/data/mal/audit-1008/work/g_reachable_cap_book_rescore/{meta,paths}/<day>.parquet` (PumpSwap PRE-trade reserves, V added to q, V in [17.5e9,17.7e9]).
- Pricing: p55_price.py logic copied (entry leg b = trigger + ceil(1.9/sps) slots, stake 0.1 SOL, PumpSwap fee tiers, 55,000 priority per send, END exit at s0 + round(330/sps) slots). Primary exit lag 2 slots (P55 realistic primary). Baseline check: with the frozen lag ceil(0.55/sps) the re-priced pnl must equal stored `pnl` on the H5 trades (max abs diff reported).
- Fail models: flat `0.85*pnl + 0.15*(-55,000)`; pressure `(1-p)*pnl + p*(-55,000)` with the stored per-trade p_fail (H5 trades). A vetoed trade is not taken and contributes 0 under both models.

## Training data (independent of the H5 trades)
Pools in the explore-0814 block (blk == explore-0814 in meta, days 2026-08-14..08-28) whose mint is NOT in the frozen H5 trade set (neither disc nor conf). On each, the H5 entry timing rule is applied by the identical P55 trigger code (first sell print with post-trade q <= 40 SOL, within 300 s of s0, BOOST-spend < budget, sps in (0.15, 0.6)); entry landing and END exit as above, exit lag 2. A pool with no trigger or with the exit before the entry landing yields no training row. Pools without an identifiable buy-only BOOST wallet are kept (their `spent` is 0, as in the code); the count with and without a BOOST wallet is reported, and if fewer than 300 training rows exist the lane stops NOT_DECIDABLE. No H5 trade is a training row, so the learned weights never see an H5 outcome. Caveat stated now: the training pools share calendar days with H5 discovery; the pools are disjoint, the days are not.

## Label (training only)
Big loser = flat-fail-model pnl at exit lag 2 <= -50% of stake (<= -50,000,000 lamports). Never used to choose the threshold or anything on the H5 sets.

## Features (6, all causal: only prints with slot STRICTLY BEFORE the trigger slot si; no creator/holder table is on the tape, so creator-sold-share and true holder share are NOT available and are replaced by wallet proxies; said plainly)
All computed over pool prints from s0 up to rows with slot < si.
1. `snipers03` = log1p(distinct trader hashes with a BUY in slots [s0, s0+3]) (drain-predictor-3 sniper 0-3 slots).
2. `sell_share` = sell SOL / (buy SOL + sell SOL) over the window (early dump pressure).
3. `max_sell_rel` = largest single sell SOL / (q+V at that print), the biggest early dump relative to depth.
4. `top_hold` = largest wallet net-token position (buy tok - sell tok, floored at 0) / sum of positive net positions (concentration proxy of top holder among observed wallets).
5. `drawdown` = (q+V at the last print before si) / max(q+V over the window) - 1 (<= 0), the pre-entry depth drawdown.
6. `nwal` = log1p(distinct trader hashes in the window).
If the window has no rows, the trade/row is dropped from both training and scoring and counted.

## Model (no tuning)
Logistic regression, features standardised with the training mean/std, L2 penalty lambda = 1.0 on standardised weights (intercept unpenalised), fit by Newton/IRLS to convergence (max 50 iters). One fit. No feature selection, no lambda search, no class weights, no re-fit after any H5 read. (sklearn is unavailable in the audit venv; numpy implementation.)

## Veto rule (fixed in advance)
Score every H5 trade with the frozen model. Threshold = 85th percentile (np.percentile, linear) of scores on the H5 DISCOVERY set (scores only, outcomes never used). Veto = score > threshold. The same threshold is applied once to confirmation. Discovery is where the definition is checked; confirmation is the test.

## Procedure
1. Counts first (no P&L): training rows by pool type, label prevalence in TRAIN (the label prevalence of the training rows is a count of training outcomes and is allowed; H5 outcomes are not touched), H5 scores, vetoed counts per set.
2. If train rows < 300, or train big-loser count < 30, or any H5 trade cannot be scored for more than 5% of a set: NOT_DECIDABLE, stop.
3. Then price and report, for discovery and confirmation, under both fail models: vetoed share; vetoed trades' mean vs kept trades' mean (pp of the 0.1 SOL stake, un-vetoed pnl); book mean per trade with and without veto and the delta; delta ex the 3 trades whose veto helps most; delta ex the best day (day with the largest total delta); placebo of a random veto with the same vetoed count, 1,000 draws, seed 1 (numpy default_rng(1)), p = share of draws with delta >= observed (one-sided). Also: days with majority positive delta, date-cluster bootstrap 5th percentile of the mean delta (1,000 draws, seed 1), and the kept book's total ex top 3 trades and ex best day, as in the brief. Sensitivity (report-only, not decisive): frozen exit lag.

## KILL lines (confirmation, either fail model)
1. Mean delta < +1.0 pp.
2. Placebo p > 0.10.
3. Delta ex top 3 <= 0 or delta ex best day <= 0.
Any one kills. Otherwise LEAD (not a pass). Wrong sign on discovery is reported, not decisive.

## Capacity line
A veto removes trades. Edge in SOL/day = (delta pp / 100) * stake * trades/day of the un-vetoed book (2,826 trades over the days present), at the frozen 0.1 SOL stake. At 0.25 and 1 SOL the delta is NOT scaled and entry impact is NOT repriced here: untested, any LEAD must be re-priced at those stakes before use.

## Seals
Aug/Sep exploration blocks via the audit-1008 paths (as P55/P77). No October hour, arena tape, DBC, LaunchLab, FLOOR G1, meta-capture, HV4/HV6/HV7/Q50 output or job, no #1419/#1421, no forward output. 0 Helius credits.
