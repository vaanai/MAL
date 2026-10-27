# P76 REPORT: PUMP hour-16 drift (library row pump-buyback-2)

**Verdict: KILL** (both kill lines hit). Plus NOT_DECIDABLE for any SOL-denominated claim: the tape has no PUMP/SOL pool.

In-sample lab dates; at least 14 families have read this block. Paper only. Nothing here says a book makes money; no book has cleared the promotion gate.

## Data
- Mint `pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn` (from the source's s1.py). The only deep pool is 2uF4X, read as PUMP/USDC (inferred, not proven). No WSOL-base pool is on the tape. A SOL trade needs SOL->USDC (Gf7sX) then USDC->PUMP.
- Held-out block: explore-0814, 2026-08-14..08-27 (source read 09-03 onward only). The rule fired on 12 of 14 days (2 skipped: fill print more than 600 s after trigger). Not enough for the gate (100 trades) in any case.

## Rule (PREREG.md, committed before pricing)
H = 16 UTC, M = 0, K = 60, one trade per day, lag 2 slots, PRE-trade reserves, fees 0.35% on 2uF4X + 0.30% SOL<->USDC per leg (about 1.30% round trip), no V (USDC pool), stakes 0.25 / 1 / 5 SOL at an assumed 150 USD/SOL. The pressure-fail model equals the flat one here (no race, p = 0.15 set; disclosed in PREREG).

## Results (`p76_output.txt`, `p76_test.py`)
| stake | gross hour-16 move | buy impact | mean net / trade (flat = pressure) | total | ex-top-3 | ex-best-day | days positive | CI90 low |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.25 SOL | +10.76 bps | 0.0356 bps | -0.002533 SOL | -0.03040 | -0.03720 | -0.03392 | 3/12 | -0.004094 |
| 1 SOL | +10.76 bps | 0.1426 bps | -0.010126 SOL | -0.12151 | -0.14873 | -0.13562 | 3/12 | -0.016371 |
| 5 SOL | +10.76 bps | 0.7129 bps | -0.051074 SOL | -0.61289 | -0.74769 | -0.68306 | 3/12 | -0.082291 |

- The source's +92.96 bps did not replicate: held-out gross is +10.76 bps, far under the 130 bps fee drag. Impact is negligible at all three stakes (PUMP/USDC holds about 10.6M USDC).
- Capacity: 1 trade/day x 1 SOL x -1.1903% = -0.011903 SOL/day. Negative at every stake.
- Placebo (same rule, 24 hours, 1 SOL, flat): hour 16 mean -0.010126 ranks **13 of 24**. Only hours 6 (+0.001975) and 21 (+0.000839) are positive, from 10-12 trades each. Top 2 line failed.
- Both kill lines hit: mean below 0 under both models, and hour 16 is not top 2.

## Not measured
SOL/USDC price move over the hold; impact on the SOL<->USDC leg; buy-side fee on 2uF4X (observable only on sells); a real PUMP/SOL pool; any October hour or sealed data (none opened); whether 99mRw is the buyback contract.
