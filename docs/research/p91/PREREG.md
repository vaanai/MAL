# P91 pre-registration: P86's numeric kNN on a low-fee (market-cap bands B5-B7) active universe (lib p91-knn-lowfee)

Written and pushed before any label (forward return) was computed. Lane P91, manager17, 2026-10-27. Paper only. Exploration data only.

**Disclosure.** The test days (fresh-0903, exp011-0909) are the same exploration days P86 used, and the memory (explore-0814) is the same block. At least 14 families have read these dates (EXP-024 sec. 9). This is in-sample probing; a positive is at best a lead, never a pass. No book has cleared the promotion gate. The P86 result (rank IC about 0.27, +6 pp over a random pick, but about -15 pp per trade once dead pools are counted) and P78 / P87 findings were known before this was written; the universe below is chosen from them, not from any P91 count.

Idea: run P86's ranker where the base rate is near zero and fees are low, so the selection gain (about +6 pp) might clear costs.

## Data and seals

Clean-view hourly trade tape for explore-0814 (w1-w7), fresh-0903 (w1-w3), exp011-0909 (b, c), PumpSwap rows only. Reused extraction: `/data2/mal/p86/ps` (P86's `p86_convert.py` output, no outcome). No October hour, arena tape, DBC, LaunchLab, FLOOR G1, meta-capture, HV4 / HV6 / HV7 / Q50 output or job. HOLDOUT_LEDGER: all three blocks are the exploration pool.

Conventions: PumpSwap tape reserves are PRE-trade (verified by P86 and P78 on these rows: 99.18% / 0.99993). Quote used = q + V. V = the P86 pool_v map value where it is inside [17.5e9, 17.7e9] lamports, else 0 (P78's rule for deep pools; the 2026-10-06 lab note says deep pools have |V| < 0.002 SOL). Rows dropped: signature 64 ones, `PHANTOM_SIGNERS` trader. Rows kept: wSOL quote, mint ends `pump` (the P78 filter). Order (slot, tx_index, event_index).

## Universe (frozen)

At every hour boundary t_d = H:02:00Z, over prints with block_time < t_d only, per pool:

1. Fee band at the last print before t_d: market cap mcap = (q + V) / b * 1e6 (the `tools/paper_curve_math` tier variable) in [58,940, 5,000,000) SOL = P78 bands B5, B6, B7 with a 5M cap.
2. Realised range over the 6 h before t_d: max / min of the pre-trade price (q+V)/b over those prints, minus 1, >= 2%.
3. P78 tradeable: >= 20 prints in the 60 min before t_d (bucket (m-2)//60 as in `p86_build.py`) and the median of (q+V) over those prints >= 100 SOL.
4. Last print within 120 s of t_d (an entry is possible); first tape print at least 6 h before t_d (so the 6 h range is a full window).
5. Real quote reserve at the last print >= 30 SOL (inherited from P86; implied by 3 for most).

Candidates = survivors ordered by `vol_60m` (gross SOL, 60 min before t_d) descending, ties by pool id; top N = 8. An hour with fewer than 3 survivors is skipped by every arm. No minimum on volume, signers or impact beyond the above. Reported before any label: candidates per hour (mean, median, p10, p90), the share of decision hours that have >= 3 candidates, and the band mix. If fewer than 100 test hours have >= 3 candidates the lane stops NOT_DECIDABLE (count floor).

## Features, label, model: P86 exactly

- 10 causal features exactly as `p86_build.py` (log_vol60, ret_5m, ret_15m, ret_60m, flow60, log_signers15, log_age, top3_60m, close_vs_hi, log_qeff); price(t) = (q+V)/b of the last print at or before t. A candidate with an undefined feature is dropped and counted.
- Label y: buy 0.25 SOL landing at slot_d + 2 (pre-trade state of the first print with slot > slot_d + 2, + V); exit trigger = first print with block_time >= t_d + 3600 inside the Amendment 1 window of 1,800 s; sell 2 slots after the trigger. Math, tier fee (exact `PUMPSWAP_SOL_FEE_TIERS` on the state, which is the pool's P78 band fee) and 2 x 55,000 lamports exactly as `p86_label.py` / `p55_price.py`. Also priced at 1.0 SOL for capacity.
- Fail models: flat 15%; pressure-fail slope scale 1 (`tools/latency_curve`, intercept fitted so mean p = 0.289 over this book's priced candidates), same-slot-buys and nearby-buy pressure as P86.
- Model: k = 25, z-score fitted on memory only, Euclidean, score = mean y of the 25 nearest. Walk-forward by UTC day: memory for day D = every priced candidate of days < D (PREREG rule as written in P86, not the strict exit-time rule; the strict rule is reported as a report-only check). Memory starts with all of explore-0814. Test days = UTC days with decision hours in fresh-0903 or exp011-0909 (decision time >= 2026-09-03T00:00Z). Memory = priced candidates only (dropped pools are not labelled); dead pools are unlabelled in memory and this is a stated limitation.
- Scoring of candidates that die: the kNN scores EVERY test candidate (features only). The book takes the top-scored candidate of the hour regardless of whether it later dies (no look-ahead selection, unlike P86's silent next-pick). A picked candidate with an entry print but no exit print is scored under both treatments, always reported side by side:
  - D1 "last price": sold at the state of the pool's last tape print after entry (same sell math, 2 sends), whatever its time;
  - D2 "-100%": pnl = -(0.25 SOL + 55,000 lamports) (stake lost, only the buy send paid).
  A candidate with no entry print at all (nothing to buy) is dropped and counted, as in P86. Fail models are applied to the dead rows' pnl as to any other.

## The book (ONE, chosen now)

Option (a): the top-scored candidate every hour that has >= 3 candidates (forced pick). Option (b) is not run as a book; only the count of hours whose top score exceeds that pool's P78 band floor (0.25 SOL round-trip floor: B5 1.0233%, B6 0.8193%, B7 0.6479%) is reported.

Baselines, same hours, same pricing, same fail models, same dead-pool treatment, 0.25 SOL: R (uniform random pick, `numpy.random.default_rng(1)`), U (mean over ALL candidates of the hour, dead included, expectation of R), S (top by vol_60m).

## Reads (test only)

Rank IC per hour on priced candidates (mean, t, n hours, day-block t) and with dead pools included (D1 / D2 labels). Book vs R vs U vs S means (pp of stake and SOL) under both fail models and both dead treatments; book mean with the top 3 trades removed; with the best UTC day removed; days positive (and majority); CI90 lower bound of mean SOL per trade (1,000 bootstrap draws, seed 1, 5th percentile, per trade; day-block report-only); trades per day; capacity at 0.25 and 1.0 SOL (trades/day x stake x net edge, impact inside the edge since picks are repriced at 1.0).

## Kill line

KILL if the book mean is <= 0 under either fail model with dead pools included (checked under BOTH D1 and D2, so four cells; any cell <= 0 kills), OR the book fails to beat U by at least +1 pp of stake in any of those four cells. Otherwise LEAD (in-sample only) if the ex-top-3 and ex-best-day totals are also > 0 in all four cells; else NOT_DECIDABLE. Promotion-gate items (>= 100 trades, >= 5 days with a majority positive, CI90 lower > 0, ex-top-3 > 0) are reported for information; in-sample dates cannot meet the gate.

## Capacity line

trades/day x stake x net edge for 0.25 and 1.0 SOL, lower of the four cells; stated as none if the mean is <= 0.

## Expectation (not evidence)

Unknown. P78 found 15% of B7 momentum trades lost more than 50% in deep pools, so the dead and crash tail may dominate; P87's filtered B7 had a small tail. The honest prior is KILL.
