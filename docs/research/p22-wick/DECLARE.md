# P22 DECLARE: wick-bid latency-0 upper bound (kill test of p20-wick-bid-zero-latency)

Written 2026-10-24T14:06:40Z, before any return, P&L or mean was computed (only candidate-event counts for one day, 2026-09-04, were printed to smoke-test the script).
Script: docs/research/p22-wick/wick_bound.py, sha256 `29096cad7864bd585b3ffdb9a8927a7d5aca75099d83d47342726dd9c37cf8b1`. Paper only, 0 Helius credits.

## Data (exploration only; docs/HOLDOUT_LEDGER.md)
`/data2/mal/blocks-clean/{explore-0814 (w1-w7), fresh-0903 (w1-w3), exp011-0909 (b,c)}`, raw PumpSwap trades, WSOL-quote pools (quote_is_wsol), mint != WSOL. Ledger status of each: explore-0814 "exploration pool" (row "Exploration expansion"); fresh-0903 spent by EXP-012's read and moved to the exploration pool 2026-10-06; exp011-0909 moved to the exploration pool by the 2026-10-06 follow-up line. Nothing from October, no arena tape, shadow output, bots.json, ledger or probe output. Side inputs: V map /data/mal/pumpswap-virtual/pool_v_{0909,exp016,0814}.json, pool_v.json (guard [0,30 SOL] else V=0), creators from the blocks' own creates files.

## Trigger (as dump-absorb DECLARE, same code path)
PumpSwap sell, seller != mint creator, pre-trade (quote+V) >= 100 SOL, drop d = 1 - P_next/P_pre with P_next the next same-pool print's pre-trade spot within 60 s. D8: d>=8%; D15: d>=15%. Every event counts as its own trade (overlaps not removed; n and days are correlated).

## Entry (the only change): latency 0
Buy stake S in {0.1, 0.25} SOL at the sell's post-trade state (next print's pre-trade reserves), constant product with V, own impact included, at event time t0. This is a resting-bid upper bound, not a fill model: no adverse selection, no queue, every trigger fills.

## Exits (dump-absorb cells, clock from t0)
D8-300, D15-300, D15-60, D15-1800: first print with block_time >= t0+hold. D15-R: first print after the sell with pre-trade spot >= P_pre within 1800 s, else first print >= t0+1800. No exit print in the loaded buffer (day + next 2 hours): censored, counted. Exit = tape reserves plus our own position.

## Costs
Gross: no fee, no sends. Net: 25 bps of SOL in on the buy and of SOL out on the sell, 505,000 lamports per send, 2 sends. No DEC-021 haircut (upper bound). Fail models via tools/arena_stack.fail_legs: flat 15% and pressure slope 1 (0.8 ln(1+ssb) + 0.35 ln(1+nearby/1e9), intercept refit per block-cell to mean p 0.289; ssb = buys in the sell's slot before it, nearby = buy lamports over the 5 slots up to it; 0.4 s slot). A failed send costs one send.

## Stats per block (explore-0814, fresh-0903, exp011-0909, pooled)
n, UTC days, days positive, mean SOL/trade, CI90 lo (arena_stack.boot_lo, trade level, 1000 draws, seed 1, 5th pct), total ex-top-3, total ex-best-day, median, total.

## Rule, declared in advance
Primary cells: D8-300 and D15-300 at 0.1 and 0.25 SOL, net, pooled, flat 15% model. If the pooled mean is <= 0 in all four, the idea p20-wick-bid-zero-latency is DEAD (library status updated with the evidence). If any is > 0 it is only "bound survives, unconfirmed": this is an upper bound, positive means come from outliers in dump-absorb, and the idea still needs ex-top-3, ex-best-day and a fill model. The other rules are reported, not rules.

## Amendment 1 (2026-10-24T14:09:43Z): memory fix only, no cell change
Job #1529 hit the 8 GB limit at the second day (log showed only candidate counts, no return or mean). The loader now returns numeric columns only (creator flag and V joined in DuckDB, pool id = hash), and the job asks 24 GB. Cells, rule and costs are unchanged. Script sha256 now `06786d30bd888938772123aacd7ab80617396ce066845f3f33fd8c5a142375e2`. Candidate counts for 2026-09-04 moved 9,768 -> 9,775 (creator test now in SQL), counts only.
