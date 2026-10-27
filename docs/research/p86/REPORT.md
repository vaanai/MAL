# P86: numeric kNN baseline on an HV7-like hourly set (lib p67-knn-case-memory). Verdict: KILL

Lane P86, 2026-10-27. Exploration data only (explore-0814, fresh-0903, exp011-0909), **in-sample lab dates read by at least 14 families**. No book has cleared the promotion gate; nothing here says a book makes money. Numbers are copied from `p86_analyze_output.txt` (primary), unrounded. `PREREG.md` was committed (0377b81f) before any label was computed; Amendment 1 (exit-print window 240 s -> 1,800 s) is disclosed there and below.

## What was done

- **Tape.** 624 clean-view hour files, PumpSwap rows only, extracted to parquet (`p86_convert.py`). PRE-trade reserve convention verified: `p86_check_reserves_output.txt` (99.18% of consecutive prints satisfy pre-trade, 3.69% post-trade).
- **Candidates** (`p86_build.py`, `p86_build_output.txt`): at each H:02, canonical WSOL pools (V in [17.5e9, 17.7e9] from the three pool_v maps), age 2 h to 7 d, real quote >= 30 SOL, >= 20 prints and >= 8 traders in 15 min, last print <= 120 s, vol_60m >= 10 SOL, impact <= 3%; top 8 by vol_60m. Per hour about 110 pools pass the screen (median 118). 620 decision hours, 4,960 candidates. HV4 chain-fact vetoes are not computable from the tape and are NOT applied, so this is "HV7-like", with a looser screen than HV7's.
- **Features** (10, causal): log vol_60m, ret 5/15/60 m, 60-min net flow share, log traders 15 m, log age, top-3 trader share 60 m, price / 6 h high, log effective quote.
- **Label** (`p86_label.py`, `p86_label_log.txt`): 0.25 SOL buy at slot_d + 2 (first print after it, pre-trade state + V), exit trigger t_d + 60 min, sell 2 slots later, `p55_price.py` fee tiers and 2 x 55,000 lamports. 4,960 candidates -> 4,600 priced; dropped and counted: 47 no entry print, 217 no trigger print within 1,800 s, 96 no exit print. Dropping dead pools is survivorship in the favourable direction, so every mean below is, if anything, flattered. Mean no-fail label over all 4,600: -0.081757.
- **Fail models:** flat 15%; pressure-fail at slope scale 1 (curve intercept -1.146258, mean p 0.2890). Pressure uses tape buys in the entry slot and in slots X-5..X (the live code uses `t_recv`, absent on backfill rows).
- **Model:** k = 25, z-scored on memory, Euclidean, mean label of neighbours; memory = all earlier UTC days (all of explore-0814 first, then test days as they pass). Book (a) = top-scored candidate each hour.
- **Sets:** memory start 2,549 candidates / 333 hours / 15 days (explore). Test 2,051 candidates / 285 hours / 13 UTC days (09-03 and 09-15 partial).

## Result (test, 0.25 SOL, exit lag 2)

**Rank IC per hour of the kNN score: mean 0.27814, t 11.294 over 285 hours; day-block t 11.893 over 13 days.** With the memory never containing the same pool (report-only, `p86_analyze_output_nopool.txt`): mean 0.27586, t 11.238, day-block t 12.660. IC is a cross-sectional ordering inside the 8 candidates; most of it is plausibly liquidity, fee-tier and concentration structure (single-feature ICs below), not timing.

| arm | flat 15%: mean pp (SOL/trade) | pressure: mean pp (SOL/trade) |
|---|---:|---:|
| kNN top pick (book) | -1.128 (-0.002821) | -0.656 (-0.001640) |
| R random pick (seed 1) | -7.997 (-0.019993) | -6.319 (-0.015797) |
| U mean of all candidates (R's expectation) | -7.493 | -5.930 |
| S vol_60m top | -18.086 (-0.045214) | -13.871 (-0.034678) |
| book - R / - U / - S | +6.869 / +6.365 / +16.957 | +5.663 / +5.274 / +13.215 |

Book detail (285 trades, 21.92 per day):

| | flat | pressure |
|---|---:|---:|
| total SOL | -0.8040 | -0.4674 |
| total without top 3 | -1.3720 (mean -1.946 pp) | -0.9697 (mean -1.375 pp) |
| total without best day (2026-09-11) | -1.0558 (mean -1.618 pp) | -0.6862 (mean -1.052 pp) |
| days positive | 7 of 13 | 7 of 13 |
| CI90 lower, mean SOL/trade (1,000 draws, seed 1) | -0.009768 | -0.007363 |
| CI90 lower, day-block | -0.009347 | -0.006774 |

Book minus U per-day: mean +6.892 pp (flat), +5.733 pp (pressure); 10 of 13 days positive in both. Baselines R and S are negative every day or nearly (R positive on 1 of 13 days, S on 0 of 13). Hours whose top kNN score is above 0: 261 of 285 (a count only; variant b was not run). The kNN pick equals the S pick in 23 of 285 hours.

Single-feature rank IC per hour (report-only, no selection; mean, t): log_vol60 -0.20605 (-8.564); ret_5m +0.12323 (+4.379); ret_15m +0.09293 (+3.269); ret_60m +0.09242 (+3.194); flow60 +0.18008 (+7.150); log_signers15 +0.06739 (+2.574); log_age -0.05465 (-2.048); top3_60m +0.21765 (+9.896); close_vs_hi +0.21480 (+8.379); log_qeff +0.19892 (+8.530). So the HV7 screen order (vol_60m descending) is anti-selective in this tape: S is the worst arm.

Capacity line (book picks repriced at 1.0 SOL): mean per trade -0.010963 SOL (-1.096 pp) flat, -0.006262 SOL (-0.626 pp) pressure; at 0.25 SOL: 21.92 trades/day x 0.25 SOL x -1.128 pp = -0.06184 SOL/day flat, -0.03595 SOL/day pressure. No positive capacity.

## Verdict: KILL

Kill line hit: the book's mean is <= 0 under both fail models (-1.128 pp, -0.656 pp). The other half (book beats R by >= +1 pp under both models) is met against R and U. So the numeric kNN is a much better picker than a random or volume-led pick, and still does not cover costs on this tape.

**Sensitivity (240 s exit window, the first run, `p86_analyze_output_w240.txt`):** 4,393 priced; IC mean 0.26355, t 9.923; book mean -0.258 pp flat and +0.301 pp pressure (ex-top-3 -1.286 / -0.597 pp), book - R +2.473 / +2.512 pp. KILL on the flat model there too. The sign flip under pressure comes only from dropping the dead pools, not from a better pick.

## For HV7 / HV8

- Numeric bar: a non-LLM kNN on 10 price/flow fields reaches a per-hour rank IC of about 0.28 on exploration data (in-sample). HV7's MDE is a rank IC of about 0.17; a numeric model already exceeds it here, so a Haiku ranker must be compared with this IC and with U, not with zero.
- The random pick R loses about 6-8 pp per trade and S about 14-18 pp, at the same pricing. HV7's L - U contrast is the right null; L - S will look large for any ranker.
- Library row p67-knn-case-memory moved to explored-no-survivor. No LLM case-memory ranker was run.

## Limits (what was not measured)

- Not HV7's screen (no chain-fact vetoes; about 110 pass per hour, not about 21 cards). Entry at slot_d + 2, not t_d + 45 s. Exit is a plain 60-min time exit, not HV7's stop -8% / target +20%.
- Pools first seen in a block are aged from the tape start, so the 7-day cap can bind only after a week.
- Dropped candidates (unpriced dead pools) bias the means upward; pressure p is a tape approximation.
- 285 hours on 13 days of dates the lab has already read; the walk-forward is a clean order, not an unread sample. Winner's-curse does not apply (one parameter set), but a LEAD would still not be a pass.
- Not judged by quant-proof.

## Files

`PREREG.md`, `p86_convert.py`, `p86_check_reserves.py` (+ output), `p86_build.py`, `p86_build_output.txt`, `p86_counts.txt`, `p86_label.py`, `p86_label_log.txt`, `p86_label_log_w240.txt`, `p86_analyze.py`, `p86_analyze_output.txt`, `p86_analyze_output_nopool.txt`, `p86_analyze_output_w240.txt`. Data (not committed): `/data2/mal/p86/`.

## Corrections after QP and reviewer review of #1043 (text only, no rerun)

The KILL is stronger than the tables above show, and the absolute levels of the book, R, U and S in them are flattered. Figures below are from QP's audit (`qp1043/audit_out.txt`, `strict_out.txt`).

1. **Survivorship.** The 360 pools dropped for lack of an exit print are excluded above. Including them (flat model, all 8 candidates per hour):

| dropped pools priced | book (pp) | U (pp) | book - U (pp) |
|---|---:|---:|---:|
| at last price | -8.185 | -14.267 | +6.081 |
| at -100% | -9.150 | -15.261 | +6.112 |

   The IC with dropped pools included is 0.274 / 0.272 (last price / -100%), or 0.244 / 0.242 if the memory is refit with them. In 28 of 286 hours the true top pick later died; the book silently took the next pick, which is a look-ahead selection. Read the levels in the main tables as upper bounds. The relative gaps (book - U) hold.
2. **Next-hour placebo.** IC 0.28802 on next-hour labels against 0.21098 on own labels, over 257 hours. The signal is a persistent pool-type property (a liquidity and structure ordering), not a one-hour forecast.
3. **Entry lag and fees.** IC 0.27814 at 2 slots, 0.27873 at +30 s, 0.27453 at +60 s. On fee-free returns 0.26370. By feature group: structural 0.30390, returns 0.23708.
4. **HV7 / HV8 bar (reworded).** The 0.28 here is for this looser set with my 10 features. The bar for HV7 / HV8 should be a numeric kNN on HV7's own cards and screen, which was not built; 0.278 is not that bar. The earlier line comparing it with HV7's MDE of 0.17 is withdrawn.
5. **Amendment 1 timing is self-attested** (see PREREG Note 2).
6. **Strict memory rule.** The PREREG sentence on 00:02 exits was wrong (PREREG Note 2). Strict rule: IC 0.27818, t 11.303; book -1.141 / -0.667 pp.
