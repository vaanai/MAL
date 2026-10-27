# P86 pre-registration: numeric kNN baseline on an HV7-like hourly candidate set (lib p67-knn-case-memory)

Written before any label (forward return) was computed. Only `p86_convert.py` (PumpSwap row extraction, no outcome) had run. One parameter set, no grid. Exploration blocks only. A positive here is a lead, never a pass; no book has cleared the promotion gate. These dates (explore-0814, fresh-0903, exp011-0909) have been read by at least 14 families (EXP-024 sec. 9), so everything is in-sample for the lab.

Purpose: HV7 (Haiku forced rank) and HV8 (offline pool ratings) need a non-LLM baseline. The deliverables whatever the verdict are the rank IC per hour and the R / S baselines at the same pricing.

## Data (seals)

- Clean-view hourly trade tape, `venue == pumpswap` rows only, blocks `explore-0814` (w1-w7), `fresh-0903` (w1-w3), `exp011-0909` (b, c). Nothing else: no October hour, no arena tape, no DBC / LaunchLab / FLOOR G1 / meta-capture, no HV4 / HV6 / HV7 / Q50 output or job. HV code (`tools/hv4_cards.py`, `tools/hv6_cards.py`) is read as source only for constants.
- V map: `/data/mal/pumpswap-virtual/pool_v_0814.json`, `pool_v.json`, `pool_v_0909.json` (read-only, merged; first non-null wins). Pricing uses `quote_reserve + V` (as `p55_price.py`).
- Rows are dropped if `signature` is 64 ones or `trader` is a `hv6_phantom.PHANTOM_SIGNERS` member. Rows are ordered by (slot, tx_index, event_index).
- Reserve convention: PumpSwap tape reserves are PRE-trade. Verified on the rows before pricing (`p86_check_reserves.py`, output committed).

## Candidate set (frozen)

At every hour boundary t_d = H:02:00Z (block_time), over prints with block_time < t_d only, per canonical pool (WSOL quote, V in [17.5e9, 17.7e9] lamports, inclusive; V missing or out of band = not a candidate):

- age since first PumpSwap print in the tape: 2 h to 7 d inclusive (`hv4_cards.AGE_MIN_S/AGE_MAX_S`); first decision hour = block start + 2 h;
- real quote reserve at the last print >= 30 SOL;
- prints in the last 15 min >= 20; distinct traders in the last 15 min >= 8; last print within 120 s of t_d;
- gross SOL volume (buy + sell) over the last 60 min (`vol_60m`) >= 10 SOL;
- round-trip impact at 0.25 SOL <= 3.0% (`hv4_cards.impact_rt` on quote_eff = real quote + V).

Candidates = survivors ordered by `vol_60m` descending (ties by pool id), top N = 8. An hour with fewer than 3 survivors is skipped by every arm (HV7 N_MIN = 3). A pool can be a candidate in consecutive hours.

Not applied (not computable from the tape): HV4 hard vetoes that need chain facts (mayhem age, holders, quote-ref staleness, sell-route) and the phantom `repeat_slot` kind. Pools that already existed at block start are aged from their first print in the tape, so the 7-day cap cannot bind in the first week of a block. These are disclosed departures; the candidate set is "HV7-like", not HV7's.

## Features (10, causal, prints before t_d only)

1. `log_vol60` = ln(1 + vol_60m SOL)
2. `ret_5m` price(t_d) / price(t_d - 5 min) - 1
3. `ret_15m`
4. `ret_60m`
5. `flow60` = (buy SOL - sell SOL) / (buy SOL + sell SOL) over 60 min
6. `log_signers15` = ln(1 + distinct traders in 15 min)
7. `log_age` = ln(age_s)
8. `top3_60m` = share of 60-min gross SOL from the 3 largest traders
9. `close_vs_hi` = price(t_d) / max price in the preceding 6 h (or the available history)
10. `log_qeff` = ln(real quote SOL + V SOL)

price(t) = (quote_reserve + V) / base_reserve of the last print at or before t (pre-trade state, a consistent proxy). A candidate with any feature undefined (no print at or before t_d - w) is dropped and counted; the count is reported.

## Label (frozen)

Entry: slot_d = slot of the last print before t_d; landing slot X = slot_d + 2 (t_e = H:02 + 2 slots). Buy fills at the pre-trade state of the first print with slot > X. Exit trigger = the first print with block_time >= t_d + 60 min; the sell lands 2 slots later (exit lag 2, the realistic primary): pre-trade state of the first print with slot > trigger_slot + 2. Pricing math, fee tier (`PUMPSWAP_SOL_FEE_TIERS` on (quote+V, base)), and 55,000 lamport priority per send (2 sends) are exactly `p55_price.py`'s `tier` / `sell`. Label y = net pnl / stake (no-fail), stake 0.25 SOL; the same pass also prices a 1.0 SOL stake for the capacity line. A candidate with no print after the exit trigger is dropped and counted (an unpriced exit is never a zero).

Fail models, applied to each trade's net pnl (lamports), entry-side as in P55:
- flat 15%: 0.85 * pnl + 0.15 * (-55,000);
- pressure-fail, slope scale 1: (1 - p) * pnl + p * (-55,000), p from `tools.latency_curve.FailCurve` with slopes 0.8 / 0.35, intercept fitted so mean p over ALL candidates of the book = 0.289 (as `fit_curve`). Pressure = same_slot_buys (tape buys with slot == X, before landing) and nearby_buy_lamports (tape buys in slots X-5..X, 5 slots ~ 2 s). This approximates `_pressure` (which uses t_recv; backfill rows have none).

The kNN label is the no-fail y; the fail models enter only when a picked trade is scored, so ranking does not depend on a fail model.

## Model (frozen: k = 25)

- Memory = every labelled candidate of earlier UTC days only (day = UTC date of t_d). Walk-forward by UTC day: memory for day D = all candidates of days < D. Train memory starts with all of explore-0814 (every candidate, labels from its own exits). Test days = every UTC day with decision hours in fresh-0903 or exp011-0909. Memory grows by test day. A day's memory never contains a candidate from the same UTC day (an exit at 00:02 of the next day is therefore never used before it exists).
- Features standardised (z-score with mean and std of the memory at that step; std 0 -> 1). Euclidean distance. Score = mean y of the k = 25 nearest memory points. Ties by memory order (stable).
- Test candidates are scored as of their day only; no hyperparameter is tuned on test.

## The book (ONE, chosen now)

Book = (a): the top-scored candidate each hour (forced pick, like HV7). Variant (b) (trade only if top score > 0) is NOT run as a book; only the number of hours whose top score exceeds 0 is reported, as a count.

Baselines, same hours, same pricing, same fail models, same 0.25 SOL:
- R: uniform random pick per hour, `numpy.random.default_rng(1)` over the hour's candidates in screen order; also its exact expectation U (mean over all candidates of the hour); the kill line below must hold against both;
- S: the top candidate by `vol_60m` (screen order 1).

## Reads (test only; fresh-0903 + exp011-0909)

- Rank IC per hour: Spearman(score, y) over the hour's candidates (hours with N >= 3); report mean, t-stat = mean / (sd / sqrt(n_hours)), n_hours, and a day-block t-stat on daily mean IC.
- Book, R, U, S, and kNN-book-minus-R / minus-U / minus-S: mean pnl per trade in pp of stake and in SOL, under both fail models.
- Mean without the top 3 trades; mean and total without the best day; count of UTC days with positive total (majority needed); CI90 lower bound of mean SOL per trade (1,000 bootstrap draws, seed 1, 5th percentile; per-trade resample as the gate, and a day-block resample report-only); trades per day; test days and trades.

## Kill line

KILL if the book's mean per trade is <= 0 under either fail model, OR the book does not beat R by at least +1 pp (of stake) under BOTH models (against the seeded R draw and against U). Otherwise LEAD (not a pass) if also ex-top-3 and ex-best-day totals are > 0 under both models; else NOT_DECIDABLE. The promotion gate (100 out-of-sample trades, 5 days majority positive, CI90 lower > 0, ex-top-3 > 0) is reported but cannot be met by in-sample dates.

## Capacity line

Reported: trades/day x stake x net edge (mean pnl per trade, both models), at 0.25 SOL and at 1.0 SOL (the same picks repriced at 1.0, so impact is inside the net edge). If the mean is <= 0 the capacity is stated as none.

## Expectation (not evidence)

Every universe the lab has measured loses before selection; the expected book mean is negative under both models. The deliverable is the IC and the R / S baselines.

## Amendment 1 (2026-10-27, before p86_analyze.py was run; after seeing the drop counts and the mean no-fail label of the first pricing run)

The PREREG did not state a maximum wait for the exit print. The first pricing run used a 240 s window after the trigger and dropped 415 candidates with no print in it (and 103 with no print after trigger + 2 slots). Dropping pools that went quiet is survivorship bias in the favourable direction (a pool that dies is exactly a loss). Primary label window is therefore 1,800 s after t_d + 3600 s (a pool with no print in that window is still dropped and counted). The 240 s run is kept as a sensitivity (`p86_label_log_w240.txt`, `p86_analyze_output_w240.txt`). The first run's mean no-fail label (-0.042288) had been seen; no other number of it had. Nothing else changed.

## Note 2 (2026-10-27, appended after QP and reviewer comments on #1043; no rerun)

- **Amendment 1 timing is self-attested.** It was written after the first pricing run's drop counts and mean label were seen, and reached git only in the results commit, not before the second pricing run. Nothing independent shows its order.
- **Correction on memory.** The text "an exit at 00:02 of the next day is therefore never used before it exists" is wrong for the rule as implemented: the day-D memory holds all candidates of days < D, and a candidate at 23:02 exits at 00:02 of day D, so for hours of day D before about 00:02 + lag its label was not yet known live. QP's strict rule (drop memory rows whose exit is after the scored hour) gives, on all 285 test hours: IC mean 0.27818, t 11.303 (day-block t 11.929); book -1.141 pp flat, -0.667 pp pressure. The top pick changes in 1 of 285 hours. The PREREG rule, not the strict one, produced the primary numbers.
