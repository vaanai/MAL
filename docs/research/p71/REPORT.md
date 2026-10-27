# P71: out-of-sample wallet persistence at MAL latency (web-public-pnl-3, web-public-pnl-5). Verdict: KILL

Lane P71, manager17, 2026-10-27. Paper only, exploration tape only. Numbers are copied from `p71_rank_output.txt`, `p71_count_output.txt` and `p71_analyze_output.txt`. These are in-sample lab dates that at least 14 families have already read (EXP-024 sec. 9), so a positive here would have been a lead, never a pass. No book has cleared the promotion gate.

## What was run

`PREREG.md` was committed first (bddb5914), then two amendments before any copy pricing (both on the August ranking step only):

1. Matched-quantity net (the literal formula credited sales of tokens not bought in the visible pool window).
2. Dropped pool-seed rows from attribution (a `sol` = 987,654,320 placeholder and prints above 100 SOL, e.g. 1,777 SOL against a pool quote of about 67 SOL, which are not swaps).

Data: audit-1008 exploration paths (PumpSwap pre-trade reserves, V in [17.5, 17.7] SOL), the same files P55 used. Each pool's tape covers about 32 min after migration, so "realized net" and the copy test cover that window only, and the 60 min time stop is truncated to the window end (all 66 timer exits are truncated).

## Ranking and bot screen (explore-0814, Aug 14-28)

- 219,707 wallets have at least 5 round-trip pools; 30,513 (13.9%) are flagged (B1 more than 30 trades per hour 25,110; B2 same-slot buy and sell 6,186; B3 fast round trips 3,654).
- The unfiltered top 50 by matched-quantity net: **44 of 50 are flagged** (B1 44, B2 10, B3 8). The clean top 50 (after exclusion, 189,194 clean ranked wallets) has Aug net from 91.8 to 402.2 SOL each, 7,790.83 SOL in total, median 23 round-trip pools.

## Copy test (Sep), 0.1 SOL, 1.9 s fill, exit at the lead's sell (same 1.9 s lag) or the time stop

611 copied buys (66 dust leads under 0.05 SOL not copied), 13 days, 32 of the 50 wallets had a copy. 545 exits were lead sells, 66 were truncated time stops. Skips for "lead sold before we landed": 0 (a lead sell is always after the lead buy, so our landing is always later).

| | n | days | mean % of stake flat 15% | mean pressure-fail | total SOL flat / press | ex top 3 flat / press | ex best day flat / press | days positive flat / press | CI90 low % flat / press |
|---|---|---|---|---|---|---|---|---|---|
| fresh-0903 | 320 | 7 | -5.77062 | -3.23792 | -1.8466 / -1.03614 | -2.73692 / -1.88639 | -2.33363 / -1.47751 | 2/7 / 2/7 | -12.93527 / -8.99108 |
| exp011-0909 | 291 | 7 | -2.95574 | -1.98778 | -0.86012 / -0.57844 | -1.79304 / -1.3643 | -1.10183 / -0.79324 | 3/7 / 3/7 | -9.09248 / -6.08692 |
| pooled | 611 | 13 | -4.42999 | -2.64252 | -2.70672 / -1.61458 | -3.84512 / -2.65021 | -3.19375 / -2.05596 | 5/13 / 5/13 | -8.73009 / -5.98689 |

Pooled without the best wallet: -3.44161 flat, -2.37124 pressure-fail. Wallets with a positive total: 9 of 32 (flat), 10 of 32 (pressure-fail). Sensitivity, copied-sell lag 2 slots (577 trades): mean -3.73575 flat, -2.07685 pressure-fail. Every cell is negative.

## Persistence

Spearman rank IC between Aug net and Sep copy net, over the 32 wallets with a copy: sum -0.1653 flat, -0.1690 press; mean per trade -0.0337 flat, -0.0521 press (p from 0.36 to 0.85, none significant). Descriptive: the same 50 wallets' own Sep realized net (matched quantity) is **-140.32 SOL** against +7,790.83 SOL in Aug; 11 of 31 wallets positive; IC -0.1690 (n=31). The August top 50 is a winner's-curse selection from 189,194 wallets and it did not persist.

## Capacity

611 copied buys over 13 Sep days = 47.00 per day (fresh-0903 320 over 7 days, exp011-0909 291 over 7 days). Repriced with the AMM impact at each stake (p_fail held at the 0.1 SOL value):

| stake | mean net % of stake flat / press | SOL per day flat / press |
|---|---|---|
| 0.1 | -4.430 / -2.643 | -0.20821 / -0.12420 |
| 0.25 | -4.373 / -2.590 | -0.51383 / -0.30436 |
| 1.0 | -4.362 / -2.582 | -2.05014 / -1.21369 |

## Kill lines (PREREG)

Line 1 hit (pooled mean below 0 under both fail models). Line 2 hit (total without the top 3 below 0 under both). Line 3 hit (total without the best wallet below 0 under both). Line 4 hit (both blocks negative under both, and without the best day still negative). Not driven by the top 3 or one wallet: it is negative everywhere.

## Not measured

Copying beyond the ~32 min post-migration window; wallets outside the 5-round-trip floor; other lag values than 1.9 s and 2 slots; real fills (this is priced on the tape, no live send); other ranking windows. Fail curve intercept is fitted on the copied sends themselves (mean p 0.289), as in the H5 scripts.
