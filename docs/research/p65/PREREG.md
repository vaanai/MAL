# P65 pre-registration: sizing overlays for the frozen Q50 (H5 BOOST-floor) rule if it goes live as a C0 canary

Library row: lib-a-A38 (sizing overlays), with cap-impact-1 for the impact curve. Written before any overlay was priced. PLANNING INPUT for the live canary. NOT gate evidence: no book has cleared the promotion gate, these are exploration blocks reused by many families (in-sample for the lab), and nothing here promotes anything.

## Population (no new tape read)
`/data/mal/hunt-1008/h5-lossfilter/frozen_binding_trades.parquet`, the 2,826 frozen binding trades priced by docs/research/p55/p55_price.py inputs: `frozen_disc` (1,702, Aug 14-28, DISCOVERY) and `frozen_conf` (1,124, Sep, CONFIRMATION). Per-trade return r = stored `flat` or `press` column / 1e8 (fraction of the 0.1 SOL stake; stored fail models). No October hour, no HV4/Q50 output, no meta-capture, DBC, FLOOR G1, LaunchLab holdout or D1. Only the parquet and cap-impact `impact.json` (an exploration summary) are opened.

## Timing (assumption, stated up front)
`mbt_utc` is taken as the pool-start time. Entry lands at `mbt_utc + trig_t + 1.9 s`; exit at `mbt_utc + 330 s + 0.55 s`. Trades are processed in entry order. Positions open from entry to exit. P&L is booked at exit time and attributed to the UTC day of the exit.

## Overlays (the whole set; no tuning, no second grid)
- Baseline B0: 0.1 SOL flat, no cap, no stop (reference only).
- (a) Flat stake 0.25 SOL (and 0.1 = B0), no cap.
- (b) Concurrency cap k in {2, 4, 8} at 0.1 SOL: skip an entry if k positions are open at its entry time.
- (c) Daily stop X in {0.1, 0.25} SOL at 0.1 SOL stake, no cap: once realized P&L booked so far on a UTC day <= -X, skip all later entries that UTC day (open positions run to exit).
- (d) Half-Kelly from DISCOVERY only: f* = argmax over f in [0, 1] (grid step 0.001) of mean(log(1 + f*r)) over the frozen_disc returns under the FLAT fail model; stake fraction = 0.5*f*, applied to the running bankroll (start 1.0 SOL, plus realized P&L). An entry is skipped if open stake plus the new stake would exceed the bankroll, or the stake would exceed 1 SOL. If f* = 0 the overlay is reported as "no stake".

## Impact at size
Per-trade return at stake s: r_s = r - (rt(s) - rt(0.1))/100, where rt(s) is the median round-trip impact in percent from cap-impact `impact.json` H5 (0.1: 0.6197, 0.25: 1.5376, 0.5: 3.0374, 1.0: 5.9389, 2.0: 11.3583), log-log interpolated, extrapolated with the last segment. The stored pnl already carries rt(0.1). Sensitivity, report-only: the p90 curve (0.8046, 1.9936, 3.9287, 7.6346, 14.4562). Both "no extra impact" (naive) and "with impact" are reported; the impact version is the one to read. Fixed fee legs are scaled with stake (second order, noted).

## Metrics (CONFIRMATION = frozen_conf only), per overlay and per fail model (flat 15%, pressure-fail slope 1)
SOL/day (total SOL / calendar days first to last conf date, inclusive; the active-day count is also printed), max drawdown of cumulative booked SOL by exit time, worst UTC day (SOL), share of candidate entries skipped (cap, stop or bankroll), ex-top-3 total SOL (drop the three largest taken trades), total SOL, n taken. Discovery results are printed for reference only.

## Reading rules
No overlay is selected, ranked into a "winner" or tuned. The table is for sizing the canary: what a stake or cap does to drawdown, skip and impact. A positive total here is exploration-pool in-sample and never "the book makes money".
