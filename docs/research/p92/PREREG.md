# P92 pre-registration: does Haiku's ranking add anything over the numeric kNN? (lib p67-knn-case-memory / HV7 cards)

Written before any rating and before any label was joined. Exploration data only (P86 test hours on fresh-0903 and exp011-0909; at least 14 families have read these dates). Offline, 0 API calls: raters are Claude-plan Haiku subagents (model haiku). The HV7 rehearsal dump (/data2/mal/hv7-rehearsal/) is NOT used. A positive here is a lead, never a pass.

## Sample (fixed by `p92_build.py`, output committed)
- Eligible hours: the 285 P86 test hours (t_d >= 2026-09-03T00Z, >= 3 labelled candidates), 13 UTC days.
- 80 hours, stratified by day: quota = largest-remainder share of 80 proportional to each day's eligible hours; within a day `numpy.random.default_rng(1).choice(..., replace=False)`. Sample sha256 61a3594e88b8fa0ea1d9097c29ce8e43cd59f86583a5bd5aa87343f9dc980e06 (list in `p92_build_output.txt`).
- Hours go to 8 batches of 10 by the same rng's permutation. Hour keys are opaque (h01..h80), card ids c1..c8 (no pool address, no time). Card order per hour: `default_rng([1, t_d]).permutation`.
- Each hour shows ALL 8 P86 candidates (top 8 by vol_60m at H:02), including pools that later die (they are causal at H:02).

## Cards
- Fields (all causal, prints before H:02): P86's 10 features mapped to RANK_v0 names (f.vol_60m, f.ret_5m, f.ret_15m, f.ret_60m, f.net_flow_60m with f.flow_ratio_60m, f.traders_15m, f.age_s, f.top3_share_60m, f.close_vs_hi_6h, f.quote_eff_sol) plus descriptive extras f.quote_sol, f.prints_15m, f.prints_60m, f.last_print_age_s. Not available in the P86 build and therefore absent (RANK_v0: "skip that line"): f.ret_1m, ret_240m, vol_5m/15m, impact_rt_*, dd_ath, risk.*, setup.type, price. No outcome field anywhere (checked by grep on the prompt files).
- System prompt: `tools/hv7_playbooks/RANK_v0.txt` verbatim except one phrase in RANKING TASK: "and sell it by a fixed code rule (stop -8%, target +20%, or 60 minutes)" is replaced by "and sell it exactly 60 minutes later", because the P86 label is a plain 60-min exit. The user message is one JSON line per hour: {"hour":..., "cards":[{"id":..., "f":{...}}]}. Prompt files: /data2/mal/p92/prompts/ (not committed; sha prefixes in build output).

## Rating
- 8 subagents (model haiku), one batch each, run concurrently. Each uses only Read (its batch + system file) and Write (its answer file); Bash and scripts are forbidden, every hour is judged by reading the cards. Output per hour: the RANK_v0 JSON array; the agent checks that it is a full permutation of the shown ids.
- An hour whose answer is not a full permutation of its 8 ids is re-asked once (new agent, that hour only). If still invalid it is excluded from ALL arms and counted. No hour is dropped for any other reason. No prompt tuning after seeing any rating.

## Scoring (after all ratings are in)
- Labels: P86 labeled.parquet (no-fail y = net pnl / 0.25 SOL stake, exit lag 2, 60-min exit, pricing as p55_price / P86). Dead pools (no priced exit in P86's window) are priced at their last print: entry as P86 (first print with slot > slot_d + 2), exit at the pre-trade state of the last print of that pool before t_d + 5400 s with slot after entry; same tier/sell math and 2 x 55,000 lamport priority. A candidate with no entry print is excluded from that hour and counted. Flat 15% and pressure-fail (P86's p; dead pools use P86's curve fitted on the labelled set) as in P86.
- kNN: the P86 walk-forward k = 25 score (memory = labelled candidates of earlier UTC days, as the P86 primary rule), computed for every candidate including dead ones.
- Per hour rank IC (Spearman, higher rank = predicted better; Haiku score = 8 - position) vs y, for: Haiku, kNN, ensemble (mean of the two ranks, ties by kNN). Hours with fewer than 3 candidates are skipped. Report mean IC, t over hours, day-block t over daily means, for all three.
- Haiku minus kNN: per-day mean of (IC_haiku - IC_knn); paired day-level two-sided sign test (binomial, zeros dropped) and day-block t; also ensemble minus kNN the same way.
- Top-pick mean return (pp of 0.25 SOL stake, both fail models, dead pools priced as above): Haiku top pick, kNN top pick, U (mean over the hour's candidates), R (uniform random pick, `default_rng(1)`, hours in order). Also with dead pools dropped (P86 convention) as a report-only line.

## Kill / verdict
- Haiku IC is not above kNN IC with p < 0.10 (sign test on days, one decision: two-sided p < 0.10 and Haiku mean > kNN mean): record "Haiku adds nothing over kNN on these cards".
- If the ensemble's mean IC beats kNN's with the same test (p < 0.10): report a LEAD (not a pass); otherwise not.
- 80 hours on 13 days is small; the sign test on 13 days has little power. A NOT_DECIDABLE caveat is written if Haiku's IC is positive but the test fails; the verdict line above is still recorded as the kill line says.
- No sentence in the report says a book makes money.

## Capacity line
Informational only: top-pick edge from the table (pp of stake) x stake x trades/day (24 hours per day if every hour traded). If the mean is <= 0 the capacity is stated as none.
