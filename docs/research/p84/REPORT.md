# P84: one day-level regime switch (prior-day graduations) on the frozen H5 binding book. Verdict: KILL (confirmation not decidable on the rule as fixed)

Lane P84, 2026-10-27. Numbers are copied from `p84_analyze_output.txt`. P&L is the P55 baseline (frozen exit, exit lag 2 slots, PumpSwap PRE-trade reserves, V 17.585 SOL, real fee tiers). Percentages are of the 0.1 SOL stake. These are in-sample lab dates that at least 14 families have already read. A positive here would be a lead, never a pass. No book has cleared the promotion gate. PREREG: `PREREG.md` (commit 02a9c2b1, before any outcome).

## What was tested
- Variable: `G(D-1)`, the graduation count of the prior UTC day (tokens.parquet `grad_ms`, count only). Chosen from library row `tod-regime-4` (graduations as an activity regime variable) and because H5 trades freshly graduated pools. Defined only if D-1 has graduations in all 24 hours; otherwise the day is OFF (undefined-off).
- Rule: trade day D only if `G(D-1)` > T. T = 4525, the median over the 13 discovery days with a defined variable (values 2977..8082). Fixed from counts.
- Tool check: re-priced frozen lag/exit equals stored pnl, max difference 0.0 lamports.

## Discovery (definition check), 1,702 trades
Days: 15, on 6 (08-16..08-21), off 9 (of which 2 undefined-off). Trades kept 699.

| model | mean all | mean on | delta pp | total all / on (SOL) | on ex-top-3 | on ex-best-day (08-17) | days positive | CI90 lo of mean | placebo p |
|---|---|---|---|---|---|---|---|---|---|
| flat 15% | 11.5930% | 11.5925% | -0.0005 | 19.7313 / 8.1032 | 5.6140 | 5.1657 | 5/6 | 5.6225% | 0.5060 |
| pressure-fail | 10.5955% | 10.8539% | +0.2584 | 18.0336 / 7.5869 | 5.6238 | 4.7645 | 6/6 | 5.6941% | 0.4710 |

On discovery the switch changes the mean per trade by about zero (placebo p 0.47 to 0.51). It only removes trading days, and the H5 mean is positive on nearly every discovery day anyway.

## Confirmation (the one-time test), 1,124 trades
Days: 21, on 0, off 21 (4 undefined-off). Trades kept 0. No mean, delta, total, CI90 or placebo exists.

Cause, known from the counts before pricing and disclosed in PREREG: the graduation count falls from 7,444 per day (Aug 15, the first complete day; Aug 14 is a 12-hour partial day with 11,180) to about 2,000 per day (mid Sep). No September prior day exceeds T = 4525 (maximum 3,610). The rule as fixed is a level test, and the level drifted below the August median for the whole confirmation set.

Kill line hit: **4 (fewer than 4 on-days; 0)**. Lines 1 to 3 have no kept trades to evaluate. Verdict KILL for this rule. Honest reading: this is a failure of the variable's stationarity, not evidence about regime switching in general. The confirmation did not test whether the switch helps.

## Report-only sensitivity S1 (declared in PREREG, never selected among, cannot give a LEAD)
Threshold = median of `G(D-1)` within each tape block. Discovery is identical to the primary (one block). Confirmation: on 8 of 21 days, 508 of 1,124 trades.

| model | mean all | mean on | delta pp | total all / on (SOL) | on ex-top-3 | on ex-best-day (09-21 flat, 09-23 press) | days positive | CI90 lo | placebo p |
|---|---|---|---|---|---|---|---|---|---|
| flat 15% | 12.5381% | 4.3426% | -8.1955 | 14.0929 / 2.2061 | 1.0071 | 1.4700 | 7/8 | 2.0783% | 0.9930 |
| pressure-fail | 10.9866% | 4.0295% | -6.9571 | 12.3489 / 2.0470 | 1.2097 | 1.3850 | 7/8 | 1.9458% | 0.9900 |

With the drift removed, the switched book is worse than the unswitched book by 7 to 8 pp per trade, and the placebo p is 0.99: random day selections beat it almost always. If anything, the days after high graduation counts (relative to the block) were the worse ones. This is report-only and declared in PREREG before pricing; it is report-only and not a finding.

## Capacity line (rough, first-order impact 2x/(Q+x), median Q = 39.48 SOL)
Discovery switched book: 116.5 trades per on-day. At a 0.25 SOL stake the first-order round-trip impact is 1.26% and at 1 SOL it is 4.94%. Per-day SOL figures from an earlier draft (including 7.75 SOL/day at 1 SOL) are withdrawn and not achievable: the stored P&L already contains impact at the 0.1 SOL stake, so subtracting the larger-stake haircut and scaling by stake double-counts impact, and the discovery mean is in-sample. No capacity or net-per-day number is claimed.

## Not measured
No tape feature other than graduation counts; no drift-robust variable in the primary; no October data; no forward or Q50/H5 output. Day-level only; 6 on-days in discovery is a small sample.

## Verdict
KILL (kill line 4: 0 on-days in confirmation). Not a LEAD. No running book is changed. The library row is updated accordingly.
